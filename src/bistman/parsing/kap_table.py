"""KAP HTML ayrıştırma — geçmiş bildirim tablosu ve bildirim gövdesi.

Neden ayrı modül: `bildirim-sorgu-sonuc?member=OID` **tek istekte 1 yıl** veriyor
(89 bildirim) ama HTML. Bu tablo backfill'in tamamı; yanlış ayrıştırma = sessizce
eksik geçmiş. Gerçek fixture'a karşı yazıldı ve testlendi
(`tests/fixtures/kap/member_history_mtrks.html`).

Tablo yapısı (17.11.2026 canlı doğrulama, 12 sütun):

    0  checkbox (+ mobil için tarih)   id=disclosureIndex
    1  #                                sıra numarası (yeniden eskiye)
    2  Tarih                            "17.11.2026 14:53" veya "Bugün 14:53"
    3  Kod                              hisse kodu — DKB'de BOŞ (ihraççı BISTECH)
    4  Unvan + konu (birleşik)
    5  Sınıf                            ÖDA / FR / DKB / DG
    6  Konu
    7  Özet
    8  İlgili hisseler                  "MTRKS" / "-" / "IDH IEV (+6)"
    9  Yıl, 10 Dönem, 11 Ek sayısı

Gövde (`attachment-detail`) ise GWT tabanlı HTML: her metin **iki kez** var,
biri `content-tr` biri `content-en` (display:none). Düz metne çevirirken İngilizce
kopyayı atmak şart — yoksa aynı tutar iki kez görünür.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from .dates import UnparsableDate, parse_kap_datetime
from .text import SYMBOL_RE

__all__ = [
    "HistoryRow",
    "KapTableError",
    "body_text",
    "html_to_text",
    "parse_history",
    "parse_related",
    "parse_when",
]

# KAP sınıf kodları (HTML tabloda Türkçe kısaltmalar)
KNOWN_CLASSES = ("öda", "oda", "fr", "dkb", "dg")

_RE_ROW = re.compile(r"<tr[^>]*>.*?</tr>", re.S)
_RE_CELL = re.compile(r"<td[^>]*>.*?</td>", re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_CHECKBOX_ID = re.compile(r'id="(\d{6,})"\s+type="checkbox"')
_RE_TRUNCATED = re.compile(r"\(\+(\d+)\)")
_RE_DATETIME = re.compile(r"(\d{2})[./](\d{2})[./](\d{4})\s+(\d{2}):(\d{2})")
_RE_RELATIVE = re.compile(r"(bugün|dün|bugun|dun)\s+(\d{1,2}):(\d{2})", re.I)
_RE_TABLE = re.compile(r"<table[^>]*>", re.I)


class KapTableError(ValueError):
    """Tablo beklenen yapıda değil — sessizce eksik geçmiş üretmektense patla."""


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Geçmiş tablosundaki tek bildirim."""

    index: int
    published_at: datetime
    disclosure_class: str | None
    company_title: str
    subject: str
    summary: str
    tickers: tuple[str, ...]
    """`Kod` sütunu = ihraççının kodu. DKB'de (BISTECH duyuruları) BOŞTUR."""
    related: tuple[str, ...] = ()
    """İlgili hisseler sütunu. Birden fazla sembol olabilir (piyasa geneli duyuru)."""
    related_total: int | None = None
    """`(+N)` varsa ilgili hisse sayısı; hisse listesi o zaman EKSİKTİR."""
    period: str | None = None
    attachment_count: int = 0

    @property
    def related_truncated(self) -> bool:
        return self.related_total is not None

    @property
    def is_relative_date(self) -> bool:
        """Tarih 'Bugün/Dün' olarak gelmişse yıl-sınırı belirsizliği vardır."""
        return False  # parse sırasında çözüldü; alan geriye dönük uyum için duruyor


# --------------------------------------------------------------- HTML -> metin


def html_to_text(raw: str) -> str:
    """HTML'i düz metne çevirir. `<br>` satır sonu olur, etiketler boşluk."""
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"</(p|div|tr|td|li)>", "\n", text, flags=re.I)
    text = _RE_TAG.sub(" ", text)
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def _drop_elements_with_class(raw: str, needle: str) -> str:
    """`class` değeri `needle` içeren elemanları (iç içe olanlarla) siler.

    KAP gövdesinde İngilizce kopya `content-en` sınıfıyla geliyor. Regex ile
    eşleşen kapanış etiketi bulunamaz (iç içe div'ler var); aynı etiket adını
    sayarak dengeli silme yapılır. Bozuk HTML'de index bulunamazsa o noktadan
    itibaren kesilir — gövde metni için kabul edilebilir, tablo için değil.
    """
    pos = 0
    while True:
        at = raw.find(needle, pos)
        if at < 0:
            return raw
        start = raw.rfind("<", 0, at)
        if start < 0:
            return raw
        tag_match = re.match(r"<([a-zA-Z][\w-]*)", raw[start:])
        if not tag_match:
            pos = at + len(needle)
            continue
        tag = tag_match.group(1).lower()
        open_re = re.compile(rf"<{tag}\b", re.I)
        close_re = re.compile(rf"</{tag}\s*>", re.I)
        depth = 0
        end = -1
        for m in re.finditer(rf"<{tag}\b|</{tag}\s*>", raw[start:], re.I):
            if m.group(0).startswith("</"):
                depth -= 1
                if depth == 0:
                    end = start + m.end()
                    break
            elif not m.group(0).endswith("/>"):
                depth += 1
        if end < 0:
            end = len(raw)
        raw = raw[:start] + raw[end:]
        pos = start
        del open_re, close_re


_RE_FIELD_MARKER = re.compile(r"^[a-z][a-z0-9]*_[A-Z][A-Za-z0-9]+\|$")
_RE_PLACEHOLDER = re.compile(r"^\[[A-Z0-9_]+\]$")


def body_text(raw: str) -> str:
    """Bildirim gövdesini düz metne çevirir.

    Üç temizlik yapılır:
      1. `content-en` kopyaları atılır (aynı tutar iki kez görünmesin),
      2. GWT alan işaretleri (`oda_ExplanationTextBlock|`) silinir,
      3. doldurulmamış yer tutucular (`[CONSOLIDATION_METHOD]`) silinir.
    """
    text = html_to_text(_drop_elements_with_class(raw, "content-en"))
    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _RE_FIELD_MARKER.match(stripped) or _RE_PLACEHOLDER.match(stripped):
            continue
        kept.append(stripped)
    return "\n".join(kept)


# ------------------------------------------------------------------ tek hücre


def parse_related(cell: str) -> tuple[tuple[str, ...], int | None]:
    """İlgili hisse hücresi -> (kodlar, toplam|None).

    >>> parse_related("MTRKS")
    (('MTRKS',), None)
    >>> parse_related("IDH IEV (+6)")
    (('IDH', 'IEV'), 8)
    """
    text = html_to_text(cell)
    if not text or text.strip() in {"-", "—"}:
        return (), None
    tickers = tuple(SYMBOL_RE.findall(text))
    m = _RE_TRUNCATED.search(text)
    total = len(tickers) + int(m.group(1)) if m else None
    return tickers, total


def parse_when(cell: str, *, today: date | None = None) -> datetime:
    """Tarih hücresi -> datetime. 'Bugün 14:53' / 'Dün 09:12' de çözülür."""
    text = html_to_text(cell)
    m = _RE_DATETIME.search(text)
    if m:
        d, mo, y, hh, mm = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d, hh, mm)
        except ValueError as exc:
            raise UnparsableDate(f"geçersiz tarih: {text!r}") from exc
    rel = _RE_RELATIVE.search(text)
    if rel:
        base = today or datetime.now().date()
        word = rel.group(1).lower()
        if word in {"dün", "dun"}:
            base = base - timedelta(days=1)
        return datetime(base.year, base.month, base.day, int(rel.group(2)), int(rel.group(3)))
    # Son çare: gün/ay/yıl biçimi (KAP detay ucunda noktalı gelir)
    try:
        return parse_kap_datetime(text)
    except UnparsableDate as exc:
        raise UnparsableDate(f"tarih çözümlenemedi: {text!r}") from exc


def _clean_title(cell: str, subject: str) -> str:
    """'Unvan + konu' birleşik sütunundan unvanı ayıklar.

    Tabloda unvan ve konu aynı hücrede: "MATRİKS ... A.Ş. Haber ve Söylentilere
    İlişkin Açıklama". Konu eklenmiş haldeyken unvan eşleştirmesi (ticker_map)
    yanlış kelime kapsamı görür; konuyu sondan kesiyoruz.
    """
    title = cell.strip()
    subj = subject.strip()
    if subj and title.endswith(subj):
        title = title[: -len(subj)].strip()
    return title


def _class_of(text: str) -> str | None:
    value = text.strip().lower()
    return value if value in KNOWN_CLASSES else None


def _cell_texts(row_html: str) -> list[str]:
    return [html_to_text(td) for td in _RE_CELL.findall(row_html)]


def _index_of(row_html: str) -> int | None:
    m = _RE_CHECKBOX_ID.search(row_html)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- tablo


def parse_history(
    raw: str | bytes,
    *,
    today: date | None = None,
    strict: bool = True,
) -> list[HistoryRow]:
    """`bildirim-sorgu-sonuc` HTML'inden bildirim listesi çıkarır.

    `today`: 'Bugün/Dün' ifadelerini mutlak tarihe çevirmek için; testte sabitlenir.
    `strict`: yapı beklenenden farklıysa (12 sütundan az) hata at. Sessizce
    eksik geçmiş üretmek, backfill'de en tehlikeli hata.
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if "<table" not in text.lower():
        raise KapTableError("geçmiş tablosu bulunamadı (HTML değil veya giriş engeli)")

    start = _RE_TABLE.search(text)
    assert start is not None
    table = text[start.start():]

    rows: list[HistoryRow] = []
    skipped = 0
    for row_html in _RE_ROW.findall(table):
        if "<td" not in row_html:  # başlık satırı (<th>)
            continue
        cells = _cell_texts(row_html)
        index = _index_of(row_html)
        if index is None or len(cells) < 12:
            skipped += 1
            continue
        relation, total = parse_related(cells[8])
        issuer = cells[3].strip().upper()
        tickers = (issuer,) if SYMBOL_RE.fullmatch(issuer) else ()
        period = cells[10].strip()
        attachment_raw = cells[11].strip()
        rows.append(
            HistoryRow(
                index=index,
                published_at=parse_when(cells[2], today=today),
                disclosure_class=_class_of(cells[5]),
                company_title=_clean_title(cells[4], cells[6]),
                subject=cells[6],
                summary=cells[7],
                tickers=tickers,
                related=relation,
                related_total=total,
                period=None if period in {"", "-", "—"} else period,
                attachment_count=int(attachment_raw) if attachment_raw.isdigit() else 0,
            )
        )

    if strict and skipped:
        raise KapTableError(
            f"{skipped} satır beklenen 12 sütun yapısında değil (KAP tablosu değişmiş olabilir)"
        )
    if strict and not rows:
        raise KapTableError("tablo bulundu ama hiç bildirim satırı ayrıştırılamadı")
    return rows


def summarize(rows: Iterable[HistoryRow]) -> dict[str, int]:
    """Sınıf bazında sayım — backfill raporu için."""
    counts: dict[str, int] = {}
    for row in rows:
        key = row.disclosure_class or "bilinmeyen"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
