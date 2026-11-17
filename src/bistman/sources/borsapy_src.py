"""borsapy adaptörü — finansal tablo satırını SEÇER ve TTM'i doğru hesaplar.

İki gerçek bulgu bu dosyayı şekillendirdi (17.11.2026 doğrulama raporu):

1. **Yanlış satır seçimi.** Kalem adında `faiz gelirleri` arayınca AKBNK'ta
   `1.7 Diğer Faiz Gelirleri` (alt kalem, 4.3 milyar) eşleşti; doğrusu
   `I. FAİZ GELİRLERİ` (üst kalem). Oran paydası 40 katı yanlış çıkardı.
   Çözüm: Roma rakamlı **üst kalem** tercih edilir, `diğer/alt` ceza alır.

2. **TTM yanlış.** Çeyreklik tablo kümülatif (YTD); borsapy 4 sütunu topluyor
   -> 2x. Hesap `analysis/ttm.py`'de; burada sadece satır seçimi ve birleştirme.

Tasarım kuralı: bu dosyada pandas **yok**. Satır seçimi etiket listesi + değer
listesi üzerinden çalışır; DataFrame'e çevirme işi en altta, tek yerde
(`fundamentals_from_frames`). Böylece seçici ağsız/veri kütüphanesiz test edilir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

from ..analysis.ttm import TTMPoint, compute_ttm, naive_borsapy_ttm
from .base import FundamentalRow

__all__ = [
    "ROLE_KEYWORDS",
    "RowPick",
    "fold",
    "parse_period_column",
    "select_row",
    "series_points",
]

Role = Literal["revenue", "net_income", "equity", "total_assets"]

# Rol başına anahtar kelimeler (ASCII katlanmış biçimde aranır).
# Sıra önemli değil; puanlama `_score` içinde.
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "satis gelirleri",
        "hasilat",
        "net satislar",
        "faiz gelirleri",
        "satis hasılati",
        "toplam gelir",
    ),
    "net_income": (
        "net donem kar",
        "net donem zarar",
        "donem kari",
        "donem kar zarari",
        "net kar",
        "ana ortaklik payi",
        "surdurulen faaliyetler",
    ),
    "equity": ("ozkaynak", "ana ortakliga ait ozkaynak"),
    "total_assets": ("toplam aktif", "aktif toplam", "toplam varlik"),
}

_ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
    "XIX": 19, "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24,
}

_FOLD_MAP = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
    }
)

_RE_TOP_LEVEL = re.compile(r"^(?P<num>[IVX]{1,5})\.\s*(?P<rest>.+)$")
_RE_SUB_LEVEL = re.compile(r"^(?P<num>\d+(?:\.\d+)+)\s*(?P<rest>.+)$")

_NEGATIVE_HINTS = ("diger", "other", "alt kalem", "ertelenmis", "matrah", "onceki donem")


def fold(text: str) -> str:
    """Türkçe karakterleri ASCII'ye indirip küçültür (arama için)."""
    folded = text.translate(_FOLD_MAP).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", folded)).strip()


@dataclass(frozen=True, slots=True)
class RowPick:
    """Seçilen satır + neden seçildiği (denetlenebilirlik)."""

    label: str | None
    score: int = 0
    is_top_level: bool = False
    candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.label is not None


def _score(label: str, role: str) -> tuple[int, bool]:
    folded = fold(label)
    keywords = ROLE_KEYWORDS[role]
    if not any(k in folded for k in keywords):
        return 0, False

    top = _RE_TOP_LEVEL.match(label.strip())
    sub = _RE_SUB_LEVEL.match(label.strip())
    score = 3

    # Üst kalem (Roma rakamlı) tercih edilir: "I. FAİZ GELİRLERİ" > "1.7 Diğer ..."
    is_top = bool(top)
    if is_top:
        score += 3
        if top and top.group("num") in _ROMAN:
            score += _ROMAN[top.group("num")]  # I. kalemler en üstte
    if sub:
        score -= 3  # "1.7 Diğer Faiz Gelirleri" gibi alt kalem

    for hint in _NEGATIVE_HINTS:
        if hint in folded:
            score -= 2

    # Tam eşleşme (katlama sonrası) ekstra puan
    if any(folded == k for k in keywords):
        score += 5
    elif any(folded.startswith(k) for k in keywords):
        score += 2
    return score, is_top


def select_row(labels: Sequence[str], role: str) -> RowPick:
    """Verilen rol için en iyi satırı seçer. Bulamazsa `label=None` (uydurma yok)."""
    if role not in ROLE_KEYWORDS:
        raise ValueError(f"bilinmeyen rol: {role}")
    scored: list[tuple[int, bool, str]] = []
    matched: list[str] = []
    for label in labels:
        score, is_top = _score(str(label), role)
        if score != 0:
            matched.append(str(label))
        if score > 0:
            scored.append((score, is_top, str(label)))
    if not scored:
        return RowPick(None, 0, False, tuple(matched)[:5] or tuple(str(l) for l in labels[:5]))
    scored.sort(key=lambda t: (-t[0], len(t[2])))  # eşit puanda kısa etiket
    best = scored[0]
    return RowPick(best[2], best[0], best[1], tuple(matched[:5]))


# ------------------------------------------------------------------ dönemler


def parse_period_column(column: object) -> tuple[int, int] | None:
    """'2026Q2' -> (2026, 2). Yıllık sütun ('2025') -> None."""
    text = str(column).upper().strip()
    m = re.fullmatch(r"(\d{4})\s*Q([1-4])", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})", text)
    if m:
        month = int(m.group(2))
        return int(m.group(1)), (month - 1) // 3 + 1
    return None


def series_points(columns: Sequence[object], values: Sequence[float]) -> list[TTMPoint]:
    """Sütun adları + değerler -> çeyrek noktaları (yalnız Q sütunları)."""
    points: list[TTMPoint] = []
    for column, value in zip(columns, values):
        parsed = parse_period_column(column)
        if parsed is None or value is None:
            continue
        year, quarter = parsed
        points.append(TTMPoint(year, quarter, float(value)))
    return points


def annual_values(columns: Sequence[object], values: Sequence[float]) -> dict[int, float]:
    """Yıllık sütunlar ('2025') -> {2025: değer}."""
    out: dict[int, float] = {}
    for column, value in zip(columns, values):
        text = str(column).strip()
        if re.fullmatch(r"\d{4}", text) and value is not None:
            out[int(text)] = float(value)
    return out


# ------------------------------------------------------------------ birleştirme


# ------------------------------------------------------------------ DataFrame


def fundamentals_from_frames(
    *,
    ticker: str,
    quarterly: object,
    annual: object | None = None,
    statement_type: str = "industrial",
    market_cap: float | None = None,
    period_end: str = "",
) -> FundamentalRow:
    """pandas DataFrame'lerden `FundamentalRow`. DataFrame'in sütunları dönemdir.

    Beklenen yön: index = kalem adı, columns = dönem ('2026Q2' / '2025').
    """
    labels = [str(i) for i in quarterly.index]  # type: ignore[attr-defined]
    columns = list(quarterly.columns)  # type: ignore[attr-defined]

    # Seçilen satırların çeyrek değerleri
    rev = select_row(labels, "revenue")
    if not rev.found:
        return FundamentalRow(
            ticker=ticker,
            period_end=period_end,
            statement_type=statement_type,
            flags=("row_not_found:revenue", "no_revenue"),
            source="borsapy/isyatirim",
        )
    row_values = _row_as_list(quarterly, rev.label)  # type: ignore[arg-type]
    points = series_points(columns, row_values)

    annual_map: dict[int, float] | None = None
    if annual is not None:
        annual_labels = [str(i) for i in annual.index]  # type: ignore[attr-defined]
        if rev.label in annual_labels:
            annual_map = annual_values(list(annual.columns), _row_as_list(annual, rev.label))  # type: ignore[arg-type]

    ni_row = select_row(labels, "net_income")
    ni_points = (
        series_points(columns, _row_as_list(quarterly, ni_row.label))  # type: ignore[arg-type]
        if ni_row.found
        else []
    )
    eq_row = select_row(labels, "equity")
    eq_points = (
        series_points(columns, _row_as_list(quarterly, eq_row.label))  # type: ignore[arg-type]
        if eq_row.found
        else []
    )

    flags: list[str] = []
    rev_ttm = compute_ttm(points, annual_map)
    flags.extend(rev_ttm.flags)
    ni_ttm = compute_ttm(ni_points, None) if ni_points else None
    if ni_ttm:
        flags.extend(f for f in ni_ttm.flags if f not in flags)
    eq_ttm = compute_ttm(eq_points, None) if eq_points else None

    if rev_ttm.value is not None:
        naive = naive_borsapy_ttm(points)
        if naive is not None and abs(naive - rev_ttm.value) / max(abs(rev_ttm.value), 1.0) > 0.01:
            flags.append("borsapy_ttm_mismatch")
    if not ni_row.found:
        flags.append("row_not_found:net_income")
    if market_cap is None:
        flags.append("no_market_cap")
    if rev_ttm.value is None:
        flags.append("no_revenue")

    return FundamentalRow(
        ticker=ticker,
        period_end=period_end,
        statement_type=statement_type,
        revenue_ttm=rev_ttm.value,
        net_income_ttm=ni_ttm.value if ni_ttm else None,
        equity=eq_ttm.value if eq_ttm else None,
        flags=tuple(dict.fromkeys(flags)),
        source="borsapy/isyatirim",
    )


def _row_as_list(frame: object, label: str) -> list[float]:
    """DataFrame satırını float listesine çevirir (pandas importu yok)."""
    row = frame.loc[label]  # type: ignore[attr-defined]
    out: list[float] = []
    for value in row.tolist() if hasattr(row, "tolist") else list(row):
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out
