"""Dış grafik alarmı için yerel KAP bağlamı. Kayıt sessiz, sorgu salt okunur.

Ağ, zamanlayıcı, grafik girdisi veya gönderim yolu yok. `known_at` bir sürümün
kanıtı ve okumasıyla birlikte ilk kullanılabilir olduğu andır, tarama saati değil.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .context_packet import Origin, PacketHeld, Topic, build_packet, compact_json
from .fundamentals import PublishedRow
from .parsing.evidence import sale_qualified
from .parsing.kap_table import body_text
from .parsing.text import SYMBOL_RE
from .pipeline import ProcessedDisclosure
from .sources.base import DisclosureDetail
from .triage import Route

DEFAULT_MAX_AGE = timedelta(days=7)  # takvim süresi; ölçülmüş bir önem eşiği değil

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alarm_versions (
    origin TEXT NOT NULL, disclosure_id INTEGER NOT NULL, version BLOB NOT NULL,
    ticker TEXT NOT NULL, published TEXT NOT NULL, known TEXT NOT NULL,
    strength INTEGER NOT NULL, valid_until TEXT, evidence TEXT, answer TEXT, held TEXT,
    CHECK ((answer IS NULL) = (evidence IS NULL) AND (answer IS NULL) != (held IS NULL)),
    PRIMARY KEY (origin, disclosure_id, version)
);
CREATE INDEX IF NOT EXISTS alarm_stock ON alarm_versions(origin, ticker, published DESC, disclosure_id DESC);
CREATE INDEX IF NOT EXISTS alarm_revision ON alarm_versions(origin, disclosure_id, strength, known);
CREATE TABLE IF NOT EXISTS alarm_replacements (
    origin TEXT NOT NULL, target INTEGER NOT NULL, replacement INTEGER NOT NULL, known TEXT NOT NULL,
    PRIMARY KEY (origin, target, replacement)
);
"""
_INSERT = """
INSERT INTO alarm_versions(origin, disclosure_id, version, ticker, published, known,
                           strength, valid_until, evidence, answer, held)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(origin, disclosure_id, version) DO UPDATE SET known = min(known, excluded.known)
"""
# Önce alarm anında bilinen sürümler arasında tek kazanan gerekir. Daha güçlü,
# daha yeni ya da eşdeğer fakat farklı bir sürüm, kendisi BEKLET/yanlış hisse/
# süresi dolmuş olsa da eski olumlu cevabı eler. Yayın sırası farklı olaylar içindir.
_SELECT = """
SELECT v.answer FROM alarm_versions v
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


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("saat dilimli bir tarih-saat gerekli (ör. +03:00)")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    # SQL metin karşılaştırmasında sabit ofset ve mikrosaniye hassasiyeti.
    return _utc(value).isoformat(timespec="microseconds")


def _ticker(value: str) -> str:
    token = value.strip() if isinstance(value, str) else ""
    symbol = token.upper().removeprefix("BIST:")
    if not token.isascii() or not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("tek BIST hisse kodu gerekli (ör. MTRKS veya BIST:MTRKS)")
    return symbol


def _check_origin(origin: Origin) -> None:
    if origin not in {"kap_arsiv", "sentetik"}:
        raise ValueError("doğrulanmamış kaynak; arşiv ile senaryo ayrı tutulur")


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
        context.update(olay=event, onemi=why)
    elif packet["konu"] == "satis_sozlesmesi":
        if packet.get("olcek") and not packet.get("sinir"):
            scale = packet["olcek"]
            pct = format(scale["bedel_ciro_yuzde"], "g").replace(".", ",")
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

    def record(
        self, item: ProcessedDisclosure, *, origin: Origin, known_at: datetime,
        topic: Topic, quote: str, detail: DisclosureDetail | None = None,
        statements: Sequence[PublishedRow] = (), valid_until: datetime | None = None,
        supersedes: int | None = None, reviewed: ReviewedReading | None = None,
    ) -> str:
        """İncelenmiş sürümü sakla: `hazir`, `kapsam_disi` veya bekletme nedeni.

        `valid_until`: varsa o sırada bilinen geçerlilik sonu. `supersedes`:
        yükleyicinin doğruladığı açık düzeltme bağlantısı; konu benzerliği yetmez.
        `reviewed` kanıt seçiminin yerine geçmez. Yazma kullanıcıya mesaj göndermez.
        """
        _check_origin(origin)
        d = item.disclosure
        published, known = _stamp(d.published_at), _stamp(known_at)
        until = _stamp(valid_until) if valid_until is not None else None
        if d.index <= 0 or known < published or (until is not None and until < published):
            raise ValueError("geçersiz bildirim kimliği veya yayın/sürüm zamanı")
        if (item.route != Route.PIPELINE or item.status not in {"scored", "no_fundamentals"}
                or not item.ticker or topic == "endeks_parametresi"):
            return "kapsam_disi"
        ticker = _ticker(item.ticker)
        if d.tickers and set(d.tickers) != {ticker}:
            raise ValueError("yapısal hisse uyuşmadı")
        if detail is not None and not detail.matches(d, ticker):
            raise ValueError("detay kaydı uyuşmadı")  # yabancı gövde var olan kaydı da bastıramaz
        body = body_text(detail.body_html) if detail is not None else ""
        body_evidence = False
        try:
            packet = build_packet(item, origin=origin, topic=topic, quote=quote,
                                  detail=detail, statements=statements)
            if packet["kanit_yeri"] == "baslik" or quote.strip() == d.subject.strip():
                raise PacketHeld("yalniz_baslik")
        except PacketHeld as exc:
            evidence = answer = None
            held = str(exc)
            # Sonraki özette bile açık fesih/koşul varsa olumlu eski bedeli verme.
            qualified = topic == "satis_sozlesmesi" and sale_qualified(d.subject, d.summary or "", body)
            strength = int(bool(body) or qualified)
        else:
            body_evidence = packet["kanit_yeri"] == "govde"
            strength = int(body_evidence)  # ilgisiz bir gövdenin varlığı, özeti güçlendirmez
            evidence, answer = compact_json(packet), compact_json(_reading(packet, reviewed))
            held = None

        # Aynı girdinin tekrar yüklenmesi yeni bir kanıt değildir. İlk kullanılabilir
        # anı koru; gerçekten değişen gövdeyi ya da eşzamanlı çelişkiyi kaybetme.
        identity = [ticker, published, until, strength, d.subject, d.summary, body,
                    evidence, answer, held, supersedes]
        version = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).digest()
        with self._db:
            self._db.execute(_INSERT, (origin, d.index, version, ticker, published, known,
                                       strength, until, evidence, answer, held))
            if supersedes is not None:
                if not body_evidence:
                    raise ValueError("düzeltme için doğrulanmış gövde kanıtı gerekli")
                self._replace(origin, supersedes, d.index, ticker, published, known)
            conflict = self._db.execute("""
                SELECT 1 FROM alarm_versions v JOIN alarm_versions other USING (origin, disclosure_id, strength, known)
                WHERE v.origin=? AND v.disclosure_id=? AND v.version=? AND other.version!=v.version LIMIT 1
            """, (origin, d.index, version)).fetchone()
        return "surum_celiskisi" if conflict else (held or "hazir")

    def _replace(self, origin: Origin, target: int, replacement: int, ticker: str, published: str, known: str) -> None:
        prior = self._db.execute(
            "SELECT DISTINCT ticker, published FROM alarm_versions WHERE origin=? AND disclosure_id=? AND known<=?",
            (origin, target, known),
        ).fetchall()
        if len(prior) != 1 or prior[0][0] != ticker or (prior[0][1], target) >= (published, replacement):
            raise ValueError("düzeltme hedefi bu hisseye ait, o anda bilinen önceki bir kayıt değil")
        # İki yazma tek transaction. Düzeltmenin süresi dolunca da eski satış dirilmez.
        self._db.execute("""
            INSERT INTO alarm_replacements(origin, target, replacement, known) VALUES (?, ?, ?, ?)
            ON CONFLICT(origin, target, replacement) DO UPDATE SET known=min(known, excluded.known)
        """, (origin, target, replacement, known))

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
        row = self._db.execute(_SELECT, dict(origin=origin, ticker=ticker, at=_stamp(at), since=_stamp(since))).fetchone()
        head = {"hisse": ticker, "alarm_zamani": at.isoformat()}
        if row is None:
            return {**head, "durum": "yok", "baglam": None, "mesaj": "İlgili güncel gelişme bulunamadı."}
        return {**head, "durum": "var", "baglam": json.loads(row[0])}


@contextmanager
def open_archive(path: str | Path, *, writable: bool = False) -> Iterator[AlarmArchive]:
    """Varsayılan salt okunur: olmayan arşivi oluşturup 'haber yok' deme."""
    if writable:
        db = sqlite3.connect(path)
    else:
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if writable:
            db.executescript(_SCHEMA)
        yield AlarmArchive(db)
    finally:
        db.close()
