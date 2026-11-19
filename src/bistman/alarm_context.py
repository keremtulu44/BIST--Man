"""Dış grafik alarmı için yerel KAP bağlamı. Kayıt sessiz, sorgu salt okunur.

Ağ, zamanlayıcı, grafik girdisi veya gönderim yolu yok. `known_at` bir sürümün
kanıtı ve okumasıyla birlikte ilk kullanılabilir olduğu andır, tarama saati değil.
Canlı kayıtta (`replay=False`) ilk ölçülen `known_at` sonradan geriye çekilemez.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .context_packet import (
    DisclosureItem,
    Origin,
    PacketHeld,
    Topic,
    build_packet,
    compact_json,
)
from .fundamentals import PublishedRow
from .parsing.evidence import polarity_inverted, sale_qualified, stoppage_contradicted
from .parsing.kap_table import body_text
from .sources.base import DisclosureDetail, DisclosureStub
from .sources.kap_companies import CompanyEntry
from .triage import Route

DEFAULT_MAX_AGE = timedelta(days=7)  # takvim süresi; ölçülmüş bir önem eşiği değil
SQLITE_BUSY_TIMEOUT_MS = 5000

# Kaynak metin hiç değişmemişken yalnız alıntı/okuma seçiminden kaynaklanan hatalar:
# aynı kaynağın önceden doğrulanmış cevabını sessizce silmemeli.
_EDITORIAL_HOLDS = frozenset({
    "alinti_kayitta_yok",
    "alinti_cumle_sinirinda_degil",
    "olay_kaniti_eksik",
    "yalniz_baslik",
    "daha_kisa_tam_kanit_sec",
    "okuma_anlam_celiskisi",
})

# Sonuç/detay/inceleme beklediği için henüz cevap veremeyen aday nedenleri.
_PENDING_HOLDS = frozenset({
    "ihale_sonucu_icin_detay_gerekli",
    "detay_bekleniyor",
    "inceleme_bekleniyor",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alarm_versions (
    origin TEXT NOT NULL, disclosure_id INTEGER NOT NULL, version BLOB NOT NULL,
    ticker TEXT NOT NULL, published TEXT NOT NULL, known TEXT NOT NULL,
    strength INTEGER NOT NULL, live INTEGER NOT NULL DEFAULT 1,
    minute_pub INTEGER NOT NULL DEFAULT 0,
    source_hash BLOB, valid_until TEXT, evidence TEXT, answer TEXT, held TEXT,
    CHECK ((answer IS NULL) = (evidence IS NULL) AND (answer IS NULL) != (held IS NULL)),
    PRIMARY KEY (origin, disclosure_id, version)
);
CREATE INDEX IF NOT EXISTS alarm_stock ON alarm_versions(origin, ticker, published DESC, disclosure_id DESC);
CREATE INDEX IF NOT EXISTS alarm_revision ON alarm_versions(origin, disclosure_id, strength, known);
CREATE TABLE IF NOT EXISTS alarm_replacements (
    origin TEXT NOT NULL, target INTEGER NOT NULL, replacement INTEGER NOT NULL,
    known TEXT NOT NULL, live INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (origin, target, replacement)
);
CREATE TABLE IF NOT EXISTS alarm_pending_replacements (
    origin TEXT NOT NULL, target INTEGER NOT NULL, replacement INTEGER NOT NULL,
    version BLOB NOT NULL, ticker TEXT NOT NULL, published TEXT NOT NULL,
    known TEXT NOT NULL, live INTEGER NOT NULL, minute_pub INTEGER NOT NULL DEFAULT 0,
    source_hash BLOB NOT NULL, valid_until TEXT, evidence TEXT NOT NULL, answer TEXT NOT NULL,
    PRIMARY KEY (origin, target, replacement, version)
);
CREATE TABLE IF NOT EXISTS alarm_scans (
    origin TEXT NOT NULL, observed TEXT NOT NULL, covered TEXT NOT NULL,
    CHECK (covered <= observed),
    PRIMARY KEY (origin, observed)
);
CREATE INDEX IF NOT EXISTS alarm_scan_lookup ON alarm_scans(origin, observed DESC);
CREATE TABLE IF NOT EXISTS alarm_seen (
    origin TEXT NOT NULL, disclosure_id INTEGER NOT NULL,
    fingerprint BLOB NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL,
    PRIMARY KEY (origin, disclosure_id)
);
CREATE TABLE IF NOT EXISTS alarm_companies (
    ticker TEXT PRIMARY KEY,
    oid TEXT NOT NULL,
    title TEXT NOT NULL,
    updated TEXT NOT NULL
);
"""

# Canlı kayıtta (live=1) ilk kullanılabilir an korunur: ne ileri ne geri kayar.
# Yalnız açıkça sırasız geçmiş oynatmasında (live=0 + excluded.live=0) en erken an tutulur.
_INSERT = """
INSERT INTO alarm_versions(origin, disclosure_id, version, ticker, published, known,
                           strength, live, minute_pub, source_hash, valid_until, evidence, answer, held)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(origin, disclosure_id, version) DO UPDATE SET
    known = CASE WHEN alarm_versions.live = 0 AND excluded.live = 0
                 THEN min(alarm_versions.known, excluded.known)
                 ELSE alarm_versions.known END,
    live = max(alarm_versions.live, excluded.live)
"""

_INSERT_REPLACEMENT = """
INSERT INTO alarm_replacements(origin, target, replacement, known, live)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(origin, target, replacement) DO UPDATE SET
    known = CASE WHEN alarm_replacements.live = 0 AND excluded.live = 0
                 THEN min(alarm_replacements.known, excluded.known)
                 ELSE alarm_replacements.known END,
    live = max(alarm_replacements.live, excluded.live)
"""

# Önce alarm anında bilinen sürümler arasında tek kazanan gerekir. Daha güçlü,
# daha yeni ya da eşdeğer fakat farklı bir sürüm, kendisi BEKLET/yanlış hisse/
# geri çekilmiş/süresi dolmuş olsa da eski olumlu cevabı eler.
_SELECT = """
SELECT v.answer, v.published, v.disclosure_id FROM alarm_versions v
WHERE v.origin=:origin AND v.ticker=:ticker AND v.published BETWEEN :since AND :at
  AND v.known <= :at AND v.answer IS NOT NULL AND (v.valid_until IS NULL OR :at < v.valid_until)
  AND NOT EXISTS (
    SELECT 1 FROM alarm_versions other
    WHERE other.origin=v.origin AND other.disclosure_id=v.disclosure_id AND other.known <= :at
      AND (other.strength, other.known) >= (v.strength, v.known) AND other.version != v.version
  )
  AND NOT EXISTS (
    SELECT 1 FROM alarm_replacements r WHERE r.origin=v.origin AND r.target=v.disclosure_id AND r.known<=:at
  )
ORDER BY v.published DESC, v.disclosure_id DESC LIMIT 1
"""

# Seçilen doğrulanmış haberden DAHA YENİ, henüz detayı/incelemesi tamamlanmamış bildirim var mı?
_SELECT_NEWER_PENDING = """
SELECT v.disclosure_id, v.published FROM alarm_versions v
WHERE v.origin=:origin AND v.ticker=:ticker AND v.published BETWEEN :since AND :at
  AND v.known <= :at
  AND v.held IN ('ihale_sonucu_icin_detay_gerekli', 'detay_bekleniyor', 'inceleme_bekleniyor')
  AND (v.valid_until IS NULL OR :at < v.valid_until)
  AND (v.published, v.disclosure_id) > (:chosen_pub, :chosen_id)
  AND NOT EXISTS (
    SELECT 1 FROM alarm_versions other
    WHERE other.origin=v.origin AND other.disclosure_id=v.disclosure_id AND other.known <= :at
      AND (other.strength, other.known) > (v.strength, v.known)
  )
  AND NOT EXISTS (
    SELECT 1 FROM alarm_replacements r WHERE r.origin=v.origin AND r.target=v.disclosure_id AND r.known<=:at
  )
ORDER BY v.published DESC, v.disclosure_id DESC LIMIT 1
"""

# Cevap çıkmadığında, pencerede bu hisse için bekleyen/çelişen güncel aday var mı?
_SELECT_STATUS = """
SELECT v.held,
       EXISTS (
         SELECT 1 FROM alarm_versions eq
         WHERE eq.origin=v.origin AND eq.disclosure_id=v.disclosure_id
           AND eq.strength=v.strength AND eq.known=v.known AND eq.version!=v.version
       ) AS conflicted
FROM alarm_versions v
WHERE v.origin=:origin AND v.ticker=:ticker AND v.published BETWEEN :since AND :at
  AND v.known <= :at AND (v.valid_until IS NULL OR :at < v.valid_until)
  AND NOT EXISTS (
    SELECT 1 FROM alarm_versions other
    WHERE other.origin=v.origin AND other.disclosure_id=v.disclosure_id AND other.known <= :at
      AND (other.strength, other.known) > (v.strength, v.known)
  )
  AND NOT EXISTS (
    SELECT 1 FROM alarm_replacements r WHERE r.origin=v.origin AND r.target=v.disclosure_id AND r.known<=:at
  )
ORDER BY v.published DESC, v.disclosure_id DESC
"""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("saat dilimli bir tarih-saat gerekli (ör. +03:00)")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    # SQL metin karşılaştırmasında sabit ofset ve mikrosaniye hassasiyeti.
    return _utc(value).isoformat(timespec="microseconds")


def _hash(parts: Sequence[object]) -> bytes:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).digest()


# BIST pay kodu: 3–5 karakter, harfle başlar ve biter (ör. SISE, THYAO, A1CAP, ISCTR).
# Rakamla biten endeks/varant kodları (XU100, THYAO1) elenir.
_EQUITY_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{1,3}[A-Z]$")


def _ticker(value: str) -> str:
    token = value.strip() if isinstance(value, str) else ""
    if not token or not token.isascii():
        raise ValueError("tek BIST hisse kodu gerekli (ör. MTRKS, BIST:MTRKS veya MTRKS.E)")
    symbol = token.upper().removeprefix("BIST:")
    if symbol.endswith(".E"):
        symbol = symbol[:-2]
    elif symbol.endswith(".IS"):
        symbol = symbol[:-3]
    if not _EQUITY_TICKER_RE.fullmatch(symbol):
        raise ValueError("tek BIST hisse kodu gerekli (ör. MTRKS, BIST:MTRKS veya MTRKS.E)")
    return symbol


def _check_origin(origin: Origin) -> None:
    if origin not in {"kap_arsiv", "sentetik"}:
        raise ValueError("doğrulanmamış kaynak; arşiv ile senaryo ayrı tutulur")


def _ensure_migrations(db: sqlite3.Connection) -> None:
    """Eski şemayla oluşturulmuş arşiv dosyalarına eksik sütunları güvenle ekler."""
    vcols = {row[1] for row in db.execute("PRAGMA table_info(alarm_versions)").fetchall()}
    if "live" not in vcols:
        db.execute("ALTER TABLE alarm_versions ADD COLUMN live INTEGER NOT NULL DEFAULT 1")
    if "minute_pub" not in vcols:
        db.execute("ALTER TABLE alarm_versions ADD COLUMN minute_pub INTEGER NOT NULL DEFAULT 0")
    if "source_hash" not in vcols:
        db.execute("ALTER TABLE alarm_versions ADD COLUMN source_hash BLOB")
    rcols = {row[1] for row in db.execute("PRAGMA table_info(alarm_replacements)").fetchall()}
    if "live" not in rcols:
        db.execute("ALTER TABLE alarm_replacements ADD COLUMN live INTEGER NOT NULL DEFAULT 1")
    if _has_table(db, "alarm_pending_replacements"):
        pcols = {
            row[1] for row in db.execute("PRAGMA table_info(alarm_pending_replacements)").fetchall()
        }
        if "minute_pub" not in pcols:
            db.execute(
                "ALTER TABLE alarm_pending_replacements "
                "ADD COLUMN minute_pub INTEGER NOT NULL DEFAULT 0"
            )


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


@dataclass(frozen=True, slots=True)
class ReviewedReading:
    """Editörün bu alıntı için yazdığı kısa okuma, alıntının kendisi değil."""

    event: str
    significance: str


def _reading(packet: dict, reviewed: ReviewedReading | None) -> dict:
    context = {"konu": packet["konu"], "olay": packet["kanit"],
               "yayin_zamani": packet["yayin"], "kaynak": packet["kaynak"]}
    if reviewed is not None:
        if packet["konu"] != "sirket_aciklamasi":
            raise ValueError("serbest okuma yalnız incelenmiş şirket açıklaması içindir")
        event, why = reviewed.event.strip(), reviewed.significance.strip()
        if not event or not why or len(event) + len(why) > 400:
            raise ValueError("olay ve önemi boş olamaz, toplam en çok 400 karakter olmalı")
        if polarity_inverted(str(packet["kanit"]), event, why):
            raise PacketHeld("okuma_anlam_celiskisi")
        context.update(olay=event, onemi=why)
    elif packet["konu"] == "satis_sozlesmesi":
        if packet.get("olcek") and not packet.get("sinir"):
            scale = packet["olcek"]
            ratio_val = float(scale["bedel_ciro_yuzde"])
            if 0 < ratio_val < 0.005:
                context["onemi"] = (
                    "Bedelin son 12 aylık ciroya oranı %0,01'den düşük; bu bir kâr tahmini değildir."
                )
            else:
                pct = format(round(ratio_val, 2), "g").replace(".", ",")
                context["onemi"] = f"Bedelin son 12 aylık ciroya oranı %{pct}; bu bir kâr tahmini değildir."
            context["mali_tablo"] = {"kaynak": scale["kaynak"], "donem_sonu": scale["donem_sonu"],
                                     "yayin_zamani": scale["tablo_yayini"]}
        elif packet.get("sinir") == "tarihli_kur_yok":
            context["onemi"] = "Teslimatlar satışları destekleyebilir; tarihli kur olmadan TL ciroyla kıyaslanamaz."
        else:
            context["onemi"] = "Teslimatlar satışları destekleyebilir; bedelin ciroya oranı doğrulanamadı."
    elif packet["konu"] == "uretim_durusu":
        context["onemi"] = "Teslimatlara etkisi duruşun süresine ve etkilenen kapasiteye bağlıdır."
    # Başka kanıt yoksa sadece tam alıntı; bağımsız neden/yön uydurma.
    return context


class AlarmArchive:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.RLock()

    def record(
        self, item: DisclosureItem, *, origin: Origin, known_at: datetime,
        topic: Topic, quote: str, detail: DisclosureDetail | None = None,
        statements: Sequence[PublishedRow] = (), valid_until: datetime | None = None,
        supersedes: int | None = None, reviewed: ReviewedReading | None = None,
        replay: bool = False, defer_missing_target: bool = False,
    ) -> str:
        """İncelenmiş sürümü sakla: `hazir`, `kapsam_disi`, `geri_cekildi` veya bekletme nedeni.

        `replay=False` (varsayılan canlı mod): sürümün ilk kaydedilen `known_at`
        saati kilitlenir; sonradan yayın saati verilse bile geriye çekilemez.
        `replay=True`: sırasız geçmiş kayıtlarını oynatırken en erken gözlemi tutar,
        ancak canlı kilitlenmiş bir kaydı yine de geriye tarihleyemez.
        `defer_missing_target=True`: `supersedes` hedefi henüz arşive girmemişse
        düzeltmeyi beklemeye alır; hedef geldiğinde doğrulayıp otomatik uygular.
        """
        _check_origin(origin)
        d = item.disclosure
        exact_from_detail = (
            detail is not None and d.minute_precision and not detail.stub.minute_precision
            and detail.matches_record(d)
        )
        eff_published = detail.stub.published_at if exact_from_detail else d.published_at
        minute_pub = int(d.minute_precision and not exact_from_detail)
        published, known = _stamp(eff_published), _stamp(known_at)
        until = _stamp(valid_until) if valid_until is not None else None
        if d.index <= 0 or known < published or (until is not None and until < published):
            raise ValueError("geçersiz bildirim kimliği veya yayın/sürüm zamanı")
        live = 0 if replay else 1

        out_of_scope = (
            item.route != Route.PIPELINE
            or item.status not in {"scored", "no_fundamentals"}
            or not item.ticker
            or topic == "endeks_parametresi"
        )
        if out_of_scope:
            if detail is not None and not detail.matches_record(d):
                raise ValueError("detay kaydı uyuşmadı")
            return self._retract_if_present(
                origin, d.index, published, known, live=live,
                minute_precision=d.minute_precision, reason="kapsam_disi_geri_cekildi",
            )

        assert item.ticker is not None
        ticker = _ticker(item.ticker)
        if d.tickers and set(d.tickers) != {ticker}:
            raise ValueError("yapısal hisse uyuşmadı")
        if detail is not None and not detail.matches(d, ticker):
            raise ValueError("detay kaydı uyuşmadı")  # yabancı gövde var olan kaydı da bastıramaz
        body = body_text(detail.body_html) if detail is not None else ""
        source_hash = _hash([ticker, published, d.subject, d.summary, body])
        body_evidence = False
        try:
            packet = build_packet(item, origin=origin, topic=topic, quote=quote,
                                  detail=detail, statements=statements)
            if packet["kanit_yeri"] == "baslik" or quote.strip() == d.subject.strip():
                raise PacketHeld("yalniz_baslik")
            answer_dict = _reading(packet, reviewed)
        except PacketHeld as exc:
            evidence = answer = None
            held = str(exc)
            qualified = (
                (topic == "satis_sozlesmesi" and sale_qualified(d.subject, d.summary or "", body))
                or (topic == "uretim_durusu" and stoppage_contradicted(d.subject, d.summary or "", body))
            )
            strength = int(bool(body) or qualified)
        else:
            body_evidence = packet["kanit_yeri"] == "govde"
            strength = int(body_evidence)  # ilgisiz bir gövdenin varlığı, özeti güçlendirmez
            evidence, answer = compact_json(packet), compact_json(answer_dict)
            held = None

        identity = [ticker, published, until, strength, d.subject, d.summary, body,
                    evidence, answer, held, supersedes]
        version = _hash(identity)

        with self._lock, self._db:
            if held in _EDITORIAL_HOLDS and supersedes is None:
                # Kaynak metin birebir aynıyken yanlış alıntı/okuma verilmişse
                # önceden doğrulanmış cevabı ezme; kaynaktaki gerçek değişiklikse yaz.
                same_source_ok = self._db.execute(
                    "SELECT 1 FROM alarm_versions WHERE origin=? AND disclosure_id=? "
                    "AND source_hash=? AND answer IS NOT NULL LIMIT 1",
                    (origin, d.index, source_hash),
                ).fetchone()
                if same_source_ok is not None:
                    return held

            if supersedes is not None:
                if not body_evidence or evidence is None or answer is None:
                    raise ValueError("düzeltme için doğrulanmış gövde kanıtı gerekli")
                status = self._prepare_replacement(
                    origin=origin, target=supersedes, replacement=d.index,
                    version=version, ticker=ticker, published=published, known=known,
                    live=live, minute_pub=minute_pub, source_hash=source_hash,
                    valid_until=until, evidence=evidence, answer=answer,
                    defer_missing=defer_missing_target,
                )
                if status == "hedef_bekleniyor":
                    return status

            self._db.execute(_INSERT, (
                origin, d.index, version, ticker, published, known,
                strength, live, minute_pub, source_hash, until, evidence, answer, held,
            ))

            if answer is not None:
                self._resolve_pending_for(origin, d.index, ticker, published, known)

            conflict = self._db.execute("""
                SELECT 1 FROM alarm_versions v
                JOIN alarm_versions other USING (origin, disclosure_id, strength, known)
                WHERE v.origin=? AND v.disclosure_id=? AND v.version=? AND other.version!=v.version
                LIMIT 1
            """, (origin, d.index, version)).fetchone()
        return "surum_celiskisi" if conflict else (held or "hazir")

    def retract(
        self, disclosure_id: int, *, published_at: datetime, known_at: datetime,
        reason: str = "yanlis_atif", origin: Origin = "kap_arsiv",
        replay: bool = False, minute_precision: bool = False,
    ) -> str:
        """Önceden yazılmış yanlış atıflı/çok hisseli kaydı öğrenildiği andan itibaren geri çeker."""
        _check_origin(origin)
        published, known = _stamp(published_at), _stamp(known_at)
        if disclosure_id <= 0 or known < published:
            raise ValueError("geçersiz bildirim kimliği veya geri çekme zamanı")
        clean_reason = reason.strip() or "yanlis_atif"
        return self._retract_if_present(
            origin, disclosure_id, published, known, live=0 if replay else 1,
            minute_precision=minute_precision, reason=clean_reason,
        )

    def _retract_if_present(
        self, origin: Origin, disclosure_id: int, published: str, known: str,
        *, live: int, minute_precision: bool, reason: str,
    ) -> str:
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT published, strength, ticker, minute_pub FROM alarm_versions "
                "WHERE origin=? AND disclosure_id=?",
                (origin, disclosure_id),
            ).fetchall()
            active = [r for r in rows if r[2] != ""]
            if not active:
                return "kapsam_disi"
            stored_pub = active[0][0]
            any_minute = minute_precision or any(bool(r[3]) for r in active)
            same_time = stored_pub == published or (
                any_minute and stored_pub[:16] == published[:16]
            )
            if not same_time:
                raise ValueError("geri çekme yayın zamanı mevcut kayıtla uyuşmadı")
            # Eski özet veya gövde kaydı tekrar oynatılsa bile geri çekmeyi aşamasın.
            strength = max(1, max(r[1] for r in active))
            version = _hash(["", published, strength, reason])
            self._db.execute(_INSERT, (
                origin, disclosure_id, version, "", published, known,
                strength, live, int(minute_precision), None, None, None, None, reason,
            ))
        return "geri_cekildi"

    def record_pending(
        self, disclosure: DisclosureStub, *, ticker: str, known_at: datetime,
        reason: str = "detay_bekleniyor", origin: Origin = "kap_arsiv", replay: bool = False,
    ) -> str:
        """Henüz detayı/incelemesi tamamlanmamış adayı cevap uydurmadan işaretler.

        `strength = -1` ile yazıldığı için aynı `known_at` anında bile doğrulanmış
        özet (`0`) veya gövde (`1`) kaydıyla çakışma üretmez; kanıt geldiği anda ezilir.
        """
        _check_origin(origin)
        symbol = _ticker(ticker)
        published, known = _stamp(disclosure.published_at), _stamp(known_at)
        if disclosure.index <= 0 or known < published:
            raise ValueError("geçersiz bildirim kimliği veya bekleme zamanı")
        if reason not in _PENDING_HOLDS:
            raise ValueError("geçersiz bekleme nedeni")
        live = 0 if replay else 1
        minute_pub = int(disclosure.minute_precision)
        version = _hash([symbol, published, -1, disclosure.subject, disclosure.summary, reason])
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT 1 FROM alarm_versions WHERE origin=? AND disclosure_id=? "
                "AND answer IS NOT NULL LIMIT 1",
                (origin, disclosure.index),
            ).fetchone()
            if existing is not None:
                return "zaten_hazir"
            self._db.execute(_INSERT, (
                origin, disclosure.index, version, symbol, published, known,
                -1, live, minute_pub, None, None, None, None, reason,
            ))
        return reason

    def record_scan(
        self, covered_until: datetime, *, observed_at: datetime | None = None,
        origin: Origin = "kap_arsiv",
    ) -> None:
        """Başarılı bir taramanın hangi yayın anına kadar kapsadığını kaydeder."""
        _check_origin(origin)
        covered = _stamp(covered_until)
        observed = _stamp(observed_at) if observed_at is not None else covered
        if covered > observed:
            raise ValueError("taranan dönem gözlem zamanını aşamaz")
        with self._lock, self._db:
            self._db.execute("""
                INSERT INTO alarm_scans(origin, observed, covered) VALUES (?, ?, ?)
                ON CONFLICT(origin, observed) DO UPDATE SET covered = max(covered, excluded.covered)
            """, (origin, observed, covered))

    def last_scan(
        self, at: datetime | None = None, *, origin: Origin = "kap_arsiv",
    ) -> datetime | None:
        """Verilen anda (veya en son) bilinen en ileri başarılı tarama zamanı."""
        _check_origin(origin)
        with self._lock:
            if not _has_table(self._db, "alarm_scans"):
                return None
            if at is None:
                row = self._db.execute(
                    "SELECT max(covered) FROM alarm_scans WHERE origin=?", (origin,)
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT max(covered) FROM alarm_scans WHERE origin=? AND observed<=?",
                    (origin, _stamp(at)),
                ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def seen_status(
        self, disclosure_id: int, fingerprint: bytes, *, origin: Origin = "kap_arsiv",
    ) -> str | None:
        """Toplayıcı tekrar kontrolü: aynı parmak iziyle işlenmişse durumunu döndürür."""
        _check_origin(origin)
        with self._lock:
            if not _has_table(self._db, "alarm_seen"):
                return None
            row = self._db.execute(
                "SELECT fingerprint, status FROM alarm_seen WHERE origin=? AND disclosure_id=?",
                (origin, disclosure_id),
            ).fetchone()
        if row is None or row[0] != fingerprint:
            return None
        return str(row[1])

    def mark_seen(
        self, disclosure_id: int, fingerprint: bytes, status: str, *,
        updated_at: datetime, origin: Origin = "kap_arsiv",
    ) -> None:
        """Toplayıcı işlem izini saklar (yeniden başlatmada gereksiz detay indirmeyi önler)."""
        _check_origin(origin)
        with self._lock, self._db:
            self._db.execute("""
                INSERT INTO alarm_seen(origin, disclosure_id, fingerprint, status, updated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(origin, disclosure_id) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    status = excluded.status,
                    updated = excluded.updated
            """, (origin, disclosure_id, fingerprint, status, _stamp(updated_at)))

    def _prepare_replacement(
        self, *, origin: Origin, target: int, replacement: int, version: bytes,
        ticker: str, published: str, known: str, live: int, minute_pub: int,
        source_hash: bytes, valid_until: str | None, evidence: str, answer: str,
        defer_missing: bool,
    ) -> str:
        if target <= 0 or target == replacement:
            raise ValueError("düzeltme hedefi bu hisseye ait, o anda bilinen önceki bir kayıt değil")
        # Hedefte daha önce hisse düzeltmesi yapılmışsa güncel kazanan sürüme bakılır.
        prior = self._db.execute("""
            SELECT DISTINCT v.ticker, v.published FROM alarm_versions v
            WHERE v.origin=? AND v.disclosure_id=? AND v.known<=?
              AND NOT EXISTS (
                SELECT 1 FROM alarm_versions other
                WHERE other.origin=v.origin AND other.disclosure_id=v.disclosure_id
                  AND other.known<=? AND (other.strength, other.known) > (v.strength, v.known)
              )
        """, (origin, target, known, known)).fetchall()
        if not prior:
            any_target = self._db.execute(
                "SELECT 1 FROM alarm_versions WHERE origin=? AND disclosure_id=? LIMIT 1",
                (origin, target),
            ).fetchone()
            if defer_missing and any_target is None:
                self._db.execute("""
                    INSERT INTO alarm_pending_replacements(
                        origin, target, replacement, version, ticker, published,
                        known, live, minute_pub, source_hash, valid_until, evidence, answer
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(origin, target, replacement, version) DO UPDATE SET
                        known = CASE WHEN alarm_pending_replacements.live = 0 AND excluded.live = 0
                                     THEN min(alarm_pending_replacements.known, excluded.known)
                                     ELSE alarm_pending_replacements.known END,
                        live = max(alarm_pending_replacements.live, excluded.live),
                        minute_pub = min(alarm_pending_replacements.minute_pub, excluded.minute_pub)
                """, (
                    origin, target, replacement, version, ticker, published,
                    known, live, minute_pub, source_hash, valid_until, evidence, answer,
                ))
                return "hedef_bekleniyor"
            raise ValueError("düzeltme hedefi bu hisseye ait, o anda bilinen önceki bir kayıt değil")
        if (
            len(prior) != 1
            or not prior[0][0]
            or prior[0][0] != ticker
            or (prior[0][1], target) >= (published, replacement)
        ):
            raise ValueError("düzeltme hedefi bu hisseye ait, o anda bilinen önceki bir kayıt değil")
        self._db.execute(_INSERT_REPLACEMENT, (origin, target, replacement, known, live))
        return "baglandi"

    def _resolve_pending_for(
        self, origin: Origin, target: int, ticker: str, published: str, known: str,
    ) -> None:
        """Sırasız gelip hedefini bekleyen düzeltmeleri hedef doğrulandığında uygular."""
        pending = self._db.execute("""
            SELECT replacement, version, ticker, published, known, live,
                   minute_pub, source_hash, valid_until, evidence, answer
            FROM alarm_pending_replacements WHERE origin=? AND target=?
        """, (origin, target)).fetchall()
        if not pending:
            return
        for (rep, ver, rep_ticker, rep_pub, rep_known, rep_live, rep_minute,
             rep_src, rep_until, rep_ev, rep_ans) in pending:
            if rep_ticker == ticker and (published, target) < (rep_pub, rep):
                # Bağ, hem hedef hem düzeltme bilindiği andan itibaren geçerlidir.
                eff_known = max(known, rep_known)
                self._db.execute(_INSERT, (
                    origin, rep, ver, rep_ticker, rep_pub, eff_known,
                    1, rep_live, rep_minute, rep_src, rep_until, rep_ev, rep_ans, None,
                ))
                self._db.execute(
                    _INSERT_REPLACEMENT, (origin, target, rep, eff_known, rep_live)
                )
            # Eşleşmeyen (yanlış hisse / eski tarih) bekleyen düzeltme yayımlanmadan düşürülür.
            self._db.execute(
                "DELETE FROM alarm_pending_replacements WHERE origin=? AND target=? "
                "AND replacement=? AND version=?",
                (origin, target, rep, ver),
            )

    def _empty_reason(
        self, origin: Origin, ticker: str, at_stamp: str, since_stamp: str,
    ) -> tuple[str, str | None]:
        params = dict(origin=origin, ticker=ticker, at=at_stamp, since=since_stamp)
        candidates = self._db.execute(_SELECT_STATUS, params).fetchall()
        if any(conflicted for _, conflicted in candidates):
            reason: str | None = "surum_celiskisi"
        elif any(held in _PENDING_HOLDS for held, _ in candidates):
            reason = "detay_bekleniyor"
        else:
            reason = None

        scanned: str | None = None
        if _has_table(self._db, "alarm_scans"):
            row = self._db.execute(
                "SELECT max(covered) FROM alarm_scans WHERE origin=? AND observed<=?",
                (origin, at_stamp),
            ).fetchone()
            scanned = row[0] if row and row[0] else None

        if reason is None:
            if scanned is None:
                reason = "tarama_kaydi_yok"
            elif scanned < at_stamp:
                reason = "tarama_geride"
            else:
                reason = "uygun_gelisme_yok"
        scan_iso = datetime.fromisoformat(scanned).isoformat() if scanned else None
        return reason, scan_iso

    def save_companies(
        self, entries: Sequence[CompanyEntry], *, updated_at: datetime | None = None,
    ) -> int:
        """KAP şirket listesini (kod, OID, unvan) yerel SQLite önbelleğine yazar."""
        if not entries:
            return 0
        stamp = _stamp(updated_at or datetime.now(timezone.utc))
        rows = [(e.ticker, e.oid, e.title, stamp) for e in entries if e.ticker and e.oid]
        if not rows:
            return 0
        with self._lock, self._db:
            self._db.execute("DELETE FROM alarm_companies")
            self._db.executemany(
                "INSERT INTO alarm_companies(ticker, oid, title, updated) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def load_companies(
        self,
        *,
        as_of: datetime | None = None,
        max_age: timedelta | None = timedelta(hours=24),
    ) -> list[CompanyEntry]:
        """Önbellekteki şirket listesini döndürür (`max_age=None` bayat yedeği de verir)."""
        with self._lock:
            if not _has_table(self._db, "alarm_companies"):
                return []
            rows = self._db.execute(
                "SELECT ticker, oid, title, updated FROM alarm_companies ORDER BY ticker"
            ).fetchall()
        if not rows:
            return []
        if max_age is not None:
            now = _utc(as_of) if as_of is not None else datetime.now(timezone.utc)
            updated = datetime.fromisoformat(rows[0][3])
            if now - updated > max_age:
                return []
        return [CompanyEntry(ticker=r[0], oid=r[1], title=r[2]) for r in rows]

    def prune(
        self,
        as_of: datetime | None = None,
        *,
        seen_days: int = 14,
        version_days: int | None = 30,
        origin: Origin | None = None,
    ) -> dict[str, int]:
        """7/24 çalışan ortamda eski imleç/tarama/sürüm kayıtlarını güvenle budar.

        Her kaynak için en son `alarm_scans` satırı her zaman korunur; böylece
        tarama damgası kaybolmaz. `version_days=None` verilirse sürümler silinmez.
        """
        if origin is not None:
            _check_origin(origin)
        if seen_days <= 0 or (version_days is not None and version_days < 8):
            raise ValueError("seen_days > 0 ve version_days >= 8 (veya None) olmalı")
        now = _utc(as_of) if as_of is not None else datetime.now(timezone.utc)
        seen_cutoff = _stamp(now - timedelta(days=seen_days))
        origins = (origin,) if origin is not None else ("kap_arsiv", "sentetik")
        deleted = {"seen": 0, "scans": 0, "pending_replacements": 0, "versions": 0, "replacements": 0}
        with self._lock, self._db:
            for org in origins:
                deleted["seen"] += self._db.execute(
                    "DELETE FROM alarm_seen WHERE origin=? AND updated < ?",
                    (org, seen_cutoff),
                ).rowcount
                deleted["scans"] += self._db.execute(
                    """
                    DELETE FROM alarm_scans WHERE origin=? AND observed < ?
                      AND observed != (SELECT max(observed) FROM alarm_scans WHERE origin=?)
                      AND covered != (SELECT max(covered) FROM alarm_scans WHERE origin=?)
                    """,
                    (org, seen_cutoff, org, org),
                ).rowcount
                deleted["pending_replacements"] += self._db.execute(
                    "DELETE FROM alarm_pending_replacements WHERE origin=? AND known < ?",
                    (org, seen_cutoff),
                ).rowcount
                if version_days is not None:
                    ver_cutoff = _stamp(now - timedelta(days=version_days))
                    deleted["versions"] += self._db.execute(
                        "DELETE FROM alarm_versions WHERE origin=? AND published < ? AND known < ?",
                        (org, ver_cutoff, ver_cutoff),
                    ).rowcount
                    deleted["replacements"] += self._db.execute(
                        """
                        DELETE FROM alarm_replacements WHERE origin=? AND known < ?
                          AND NOT EXISTS (
                            SELECT 1 FROM alarm_versions v
                            WHERE v.origin=alarm_replacements.origin
                              AND v.disclosure_id=alarm_replacements.target
                          )
                        """,
                        (org, ver_cutoff),
                    ).rowcount
        return deleted

    def alarm_baglami(
        self, hisse: str, alarm_zamani: datetime, *, max_age: timedelta = DEFAULT_MAX_AGE,
        origin: Origin = "kap_arsiv",
    ) -> dict[str, object]:
        """Tek dış alarma en çok bir kaynaklı gelişme. Yazma, gönderim, ağ erişimi yok."""
        _check_origin(origin)
        ticker, at = _ticker(hisse), _utc(alarm_zamani)
        if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
            raise ValueError("güncellik penceresi pozitif olmalı")
        earliest = datetime.min.replace(tzinfo=timezone.utc)
        since = at - min(max_age, at - earliest)
        at_stamp, since_stamp = _stamp(at), _stamp(since)
        with self._lock:
            row = self._db.execute(
                _SELECT, dict(origin=origin, ticker=ticker, at=at_stamp, since=since_stamp)
            ).fetchone()
            head: dict[str, object] = {"hisse": ticker, "alarm_zamani": at.isoformat()}
            if row is not None:
                newer = self._db.execute(
                    _SELECT_NEWER_PENDING,
                    dict(origin=origin, ticker=ticker, at=at_stamp, since=since_stamp,
                         chosen_pub=row[1], chosen_id=row[2]),
                ).fetchone()
                out_var: dict[str, object] = {**head, "durum": "var", "baglam": json.loads(row[0])}
                if newer is not None:
                    p_id, p_pub = int(newer[0]), str(newer[1])
                    ref = (f"https://www.kap.org.tr/tr/Bildirim/{p_id}"
                           if origin == "kap_arsiv" else f"senaryo:{p_id}")
                    out_var["bekleyen_bildirim"] = {
                        "kaynak": ref,
                        "yayin_zamani": datetime.fromisoformat(p_pub).isoformat(),
                        "neden": "detay_bekleniyor",
                    }
                return out_var
            reason, scan_iso = self._empty_reason(origin, ticker, at_stamp, since_stamp)
        out: dict[str, object] = {
            **head,
            "durum": "yok",
            "baglam": None,
            "neden": reason,
            "mesaj": "İlgili güncel gelişme bulunamadı.",
        }
        if scan_iso is not None:
            out["son_tarama"] = scan_iso
        return out


@contextmanager
def open_archive(path: str | Path, *, writable: bool = False) -> Iterator[AlarmArchive]:
    """Varsayılan salt okunur: olmayan arşivi oluşturup 'haber yok' deme.

    Yazılabilir bağlantıda WAL ve `busy_timeout` açılır; böylece arka plan
    toplayıcısı yazarken grafik alarmı sorgusu kilitlenmeden okuyabilir.
    """
    if writable:
        db = sqlite3.connect(
            path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, check_same_thread=False
        )
    else:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(
            uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, check_same_thread=False
        )
    try:
        db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        if writable:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(_SCHEMA)
            _ensure_migrations(db)
        else:
            db.execute("PRAGMA query_only = ON")
        yield AlarmArchive(db)
    finally:
        if writable:
            try:
                # PASSIVE açık okuyucuları bloklamadan hazır sayfaları aktarır.
                db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
        db.close()


def kap_baglami_ekle(
    yorum_istegi: dict[str, object],
    arsiv_yolu: str | Path,
    hisse: str,
    tetik_zamani: datetime,
    *,
    max_age: timedelta = DEFAULT_MAX_AGE,
    origin: Origin = "kap_arsiv",
    logger: logging.Logger | None = None,
) -> dict[str, object] | None:
    """Ana sistemin mevcut grafik alarmı yorum isteğine doğrulanmış KAP bağlamı ekler.

    - `durum == "var"` ise `yorum_istegi["kap_baglami"]` doldurulur.
    - `durum == "yok"` veya hata durumunda önceki çağrıdan kalmış `kap_baglami` /
      `kap_bekleyen_bildirim` anahtarları temizlenir (yeniden kullanılan sözlük güvenliği).
    - Arşiv/girdi hatasında `'haber yok'` uydurulmaz; hata loglanır, `None` döner ve
      grafik alarmı kendi akışına kesintisiz devam eder.
    """
    yorum_istegi.pop("kap_baglami", None)
    yorum_istegi.pop("kap_bekleyen_bildirim", None)
    try:
        with open_archive(arsiv_yolu) as arsiv:
            sonuc = arsiv.alarm_baglami(hisse, tetik_zamani, max_age=max_age, origin=origin)
    except (ValueError, OSError, sqlite3.Error) as hata:
        (logger or logging.getLogger(__name__)).warning(
            "%s için KAP bağlamı okunamadı: %s", hisse, hata
        )
        return None
    if sonuc["durum"] == "var":
        yorum_istegi["kap_baglami"] = sonuc["baglam"]
        if "bekleyen_bildirim" in sonuc:
            yorum_istegi["kap_bekleyen_bildirim"] = sonuc["bekleyen_bildirim"]
    return sonuc
