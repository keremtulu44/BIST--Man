"""İş Yatırım `MaliTablo` yanıtı — satır seçimi, tablo ayrımı ve TTM.

Neden "borsapy" değil: borsapy'yi bıraktık (kurulumu kırılgan, TTM'i 2x hatalı).
Kalan değerli kısım bu dosyadaki saf mantık; HTTP katmanı `isyatirim_client.py`.

Üç gerçek bulgu bu dosyayı şekillendirdi (17.11.2026, gerçek yanıtlar):

1. **Yanlış satır seçimi.** Kalem adında `faiz gelirleri` arayınca AKBNK'ta
   `1.7 Diğer Faiz Gelirleri` (alt kalem, 4.3 milyar) eşleşti; doğrusu
   `I. FAİZ GELİRLERİ`. Oran paydası 40 katı yanlış çıkardı.

İki gerçek bulgu bu dosyayı şekillendirdi (17.11.2026 doğrulama raporu):

1. **Yanlış satır seçimi.** Kalem adında `faiz gelirleri` arayınca AKBNK'ta
   `1.7 Diğer Faiz Gelirleri` (alt kalem, 4.3 milyar) eşleşti; doğrusu
   `I. FAİZ GELİRLERİ` (üst kalem). Oran paydası 40 katı yanlış çıkardı.
   Çözüm: Roma rakamlı **üst kalem** tercih edilir, `diğer/alt` ceza alır.

2. **TTM yanlış.** Çeyreklik tablo kümülatif (YTD); borsapy 4 sütunu topluyor
   -> 2x. Hesap `analysis/ttm.py`'de.

3. **Uç üç tabloyu birden döndürüyor** (bilanço 1xxx/2xxx, gelir 3xxx, nakit akışı
   4xxx). THYAO 133, AKBNK 192 kalem. Filtresiz seçim nakit akışındaki
   `Dönem Karı Vergi Yükümlülüğü`'nü net kâr sanar. `ROLE_TABLE` ile her rol
   kendi tablosundan seçilir.

4. **Her kalem akış değil**: özkaynak/aktif STOK'tur, TTM'i olmaz -> son değer.

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
    "ROLE_KIND",
    "ROLE_TABLE",
    "RowPick",
    "TABLE_PREFIX",
    "fold",
    "parse_period_column",
    "select_row",
    "series_points",
]

Role = Literal["revenue", "net_income", "equity", "total_assets"]

# itemCode ilk hanesi -> tablo (gerçek yanıtta doğrulandı: THYAO 23/33/43/34, AKBNK 66/64/62)
TABLE_PREFIX: dict[str, str] = {
    "1": "balance_assets",
    "2": "balance_liabilities",
    "3": "income",
    "4": "cashflow",
}

# Rol hangi tablodan seçilir
ROLE_TABLE: dict[str, str] = {
    "revenue": "income",
    "net_income": "income",
    "equity": "balance_liabilities",
    "total_assets": "balance_assets",
}

# Rol akış mı stok mu: stok kalemde TTM hesaplanmaz, son bilanço değeri alınır
ROLE_KIND: dict[str, str] = {
    "revenue": "flow",
    "net_income": "flow",
    "equity": "stock",
    "total_assets": "stock",
}

# Rol başına anahtar kelimeler (ASCII katlanmış biçimde aranır).
# Sıra önemli değil; puanlama `_score` içinde.
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "satis gelirleri",
        "hasilat",
        "net satislar",
        "faiz gelirleri",
        "satis hasilati",
        "toplam gelir",
    ),
    "net_income": (
        "donem kari zarari",
        "net donem kar",
        "donem net kar",
        "net donem zarar",
        "ana ortaklik paylari",
        "ana ortaklik payi",
        "donem kari",
        "net kar",
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

# Sert elemeler: bu kelimeleri içeren kalem, o rol için ASLA seçilmez.
# Gerçek fixture'da (THYAO, 133 kalem) `Dönem Karı Vergi Yükümlülüğü` net kâr
# sanılmıştı; 0.5 milyar TL'lik vergi kalemi, ~90 milyar TL'lik kârın yerine geçti.
_HARD_NEGATIVE = (
    "vergi",
    "yukumlul",
    "ertelenmis",
    "matrah",
    "gecmis yil",
    "durdurulan",
    "dagilim",
    "karsilig",
    "karsilik",
)

# Yumuşak cezalar: elenmez ama üst kalemin gerisinde kalır.
_SOFT_NEGATIVE = ("diger", "other", "alt kalem", "onceki donem")


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

    if any(hint in folded for hint in _HARD_NEGATIVE):
        return 0, False  # sert eleme
    for hint in _SOFT_NEGATIVE:
        if hint in folded:
            score -= 2

    # Tam eşleşme (katlama sonrası) ekstra puan
    if any(folded == k for k in keywords):
        score += 5
    elif any(folded.startswith(k) for k in keywords):
        score += 2
    return score, is_top


def select_row(
    labels: Sequence[str],
    role: str,
    codes: Sequence[str] | None = None,
) -> RowPick:
    """Verilen rol için en iyi satırı seçer. Bulamazsa `label=None` (uydurma yok).

    `codes` verilirse (itemCode listesi) önce **tablo filtresi** uygulanır: gelir
    kalemleri bilanço/nakit akışıyla karışmaz. Gerçek veride bu filtre olmadan
    net kâr yerine nakit akışındaki vergi kalemi seçilmişti.
    """
    if role not in ROLE_KEYWORDS:
        raise ValueError(f"bilinmeyen rol: {role}")

    pool = list(labels)
    if codes is not None and len(codes) == len(labels):
        table = ROLE_TABLE[role]
        filtered = [
            label for label, code in zip(labels, codes)
            if TABLE_PREFIX.get(str(code)[:1]) == table
        ]
        if filtered:  # tablo boşsa (ör. bankada nakit akışı yok) tümüne düş
            pool = filtered

    scored: list[tuple[int, bool, str]] = []
    matched: list[str] = []
    for label in pool:
        score, is_top = _score(str(label), role)
        if score != 0:
            matched.append(str(label))
        if score > 0:
            scored.append((score, is_top, str(label)))
    if not scored:
        return RowPick(None, 0, False, tuple(matched)[:5] or tuple(str(l) for l in pool[:5]))
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


# --------------------------------------------------- JSON kalemleri (üretim yolu)


def _item_points(item: dict, months: Sequence[int] | None = None) -> list[TTMPoint]:
    """{"values": {(yıl, ay): değer}} -> TTMPoint listesi (ay // 3 = çeyrek)."""
    points: list[TTMPoint] = []
    for (year, month), value in (item.get("values") or {}).items():
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        points.append(TTMPoint(int(year), int(month) // 3, numeric))
    return sorted(points, key=lambda p: (p.year, p.quarter))


def fundamentals_from_items(
    *,
    ticker: str,
    quarterly: Sequence[dict],
    annual: Sequence[dict] | None = None,
    statement_type: str = "industrial",
    market_cap: float | None = None,
    period_end: str = "",
    source: str = "isyatirim/malitablo",
) -> FundamentalRow:
    """`MaliTablo` kalemlerinden `FundamentalRow` (pandas yok — üretim yolu).

    Kalem biçimi: {"label": str, "code": str, "values": {(yıl, ay): değer}}.
    Ay, çeyreğe çevrilir (3->Q1, 6->Q2, 9->Q3, 12->Q4) ve tablo ayrımı itemCode
    ile yapılır.
    """
    labels = [str(i.get("label", "")) for i in quarterly]
    codes = [str(i.get("code", "")) for i in quarterly]
    by_label = {str(i.get("label", "")): i for i in quarterly}
    annual_by_label = {str(i.get("label", "")): i for i in (annual or [])}
    flags: list[str] = []
    values: dict[str, float | None] = {}

    for role in ("revenue", "net_income", "equity"):
        pick = select_row(labels, role, codes)
        if not pick.found:
            flags.append(f"row_not_found:{role}")
            values[role] = None
            continue

        points = _item_points(by_label[pick.label])  # type: ignore[index]
        annual_map: dict[int, float] | None = None
        if role == "revenue" and pick.label in annual_by_label:
            annual_map = {
                p.year: p.value
                for p in _item_points(annual_by_label[pick.label])
                if p.quarter == 4
            }

        result = compute_ttm(points, annual_map, kind=ROLE_KIND[role])  # type: ignore[arg-type]
        values[role] = result.value
        flags.extend(f for f in result.flags if f not in flags)

        if role == "revenue" and result.value is not None and ROLE_KIND["revenue"] == "flow":
            naive = naive_borsapy_ttm(points)
            if naive is not None and abs(naive - result.value) / max(abs(result.value), 1.0) > 0.01:
                flags.append("naive_sum_mismatch")

    if values["revenue"] is None:
        flags.append("no_revenue")
    if market_cap is None:
        flags.append("no_market_cap")

    return FundamentalRow(
        ticker=ticker,
        period_end=period_end,
        statement_type=statement_type,
        revenue_ttm=values["revenue"],
        net_income_ttm=values["net_income"],
        equity=values["equity"],
        flags=tuple(dict.fromkeys(flags)),
        source=source,
    )


# ------------------------------------------------------------------ DataFrame


def fundamentals_from_frames(
    *,
    ticker: str,
    quarterly: object,
    annual: object | None = None,
    statement_type: str = "industrial",
    market_cap: float | None = None,
    period_end: str = "",
    codes: Sequence[str] | None = None,
) -> FundamentalRow:
    """pandas DataFrame'lerden `FundamentalRow`. DataFrame'in sütunları dönemdir.

    Beklenen yön: index = kalem adı, columns = dönem ('2026Q2' / '2025').
    `codes`: index ile hizalı itemCode listesi — verilirse tablo ayrımı yapılır
    (gelir/bilanço/nakit akışı). Verilmezse karışık seçim riski vardır.
    """
    labels = [str(i) for i in quarterly.index]  # type: ignore[attr-defined]
    columns = list(quarterly.columns)  # type: ignore[attr-defined]

    # Seçilen satırların çeyrek değerleri
    rev = select_row(labels, "revenue", codes)
    if not rev.found:
        return FundamentalRow(
            ticker=ticker,
            period_end=period_end,
            statement_type=statement_type,
            flags=("row_not_found:revenue", "no_revenue"),
            source="isyatirim/malitablo",
        )
    row_values = _row_as_list(quarterly, rev.label)  # type: ignore[arg-type]
    points = series_points(columns, row_values)

    annual_map: dict[int, float] | None = None
    if annual is not None:
        annual_labels = [str(i) for i in annual.index]  # type: ignore[attr-defined]
        if rev.label in annual_labels:
            annual_map = annual_values(list(annual.columns), _row_as_list(annual, rev.label))  # type: ignore[arg-type]

    ni_row = select_row(labels, "net_income", codes)
    ni_points = (
        series_points(columns, _row_as_list(quarterly, ni_row.label))  # type: ignore[arg-type]
        if ni_row.found
        else []
    )
    eq_row = select_row(labels, "equity", codes)
    eq_points = (
        series_points(columns, _row_as_list(quarterly, eq_row.label))  # type: ignore[arg-type]
        if eq_row.found
        else []
    )

    flags: list[str] = []
    rev_ttm = compute_ttm(points, annual_map, kind=ROLE_KIND["revenue"])
    flags.extend(rev_ttm.flags)
    ni_ttm = compute_ttm(ni_points, None, kind=ROLE_KIND["net_income"]) if ni_points else None
    if ni_ttm:
        flags.extend(f for f in ni_ttm.flags if f not in flags)
    eq_ttm = (
        compute_ttm(eq_points, None, kind=ROLE_KIND["equity"]) if eq_points else None
    )

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
        source="isyatirim/malitablo",
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
