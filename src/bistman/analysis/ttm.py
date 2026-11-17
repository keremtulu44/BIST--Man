"""TTM hesabı — çeyrek verisinin KÜMÜLATİF mi TEKİL mi olduğunu tespit eder.

Neden bu modül var (17.11.2026 doğrulama bulgusu, THYAO + AKBNK gerçek verisi):

    borsapy'nin `ttm_income_stmt`'i şöyle çalışıyor:
        TTM = quarterly_df.iloc[:, :4].sum(axis=1)          # son 4 sütunu toplar

    Ama İş Yatırım'ın çeyreklik gelir tablosu **KÜMÜLATİF (YTD)**:
        2026Q1 = ilk 3 ay · 2026Q2 = ilk 6 ay · 2025Q4 = 12 ayın tamamı

    Bu yüzden 4 sütunu toplamak yıl içi dönemleri ÇİFT sayar. THYAO'da
    borsapy TTM'i 2.72 trilyon TL çıktı; gerçek TTM ~1.36 trilyon.
    **Fark 2.00x** — ve yönü de bilinmiyor, çünkü haber tutarı bu sayıya bölünüyor:
    oran yarıya düşer, haber olduğundan küçük görünür, sistem susar.

    Kritik ders: borsapy TTM'i KENDİ İÇİNDE tutarlıydı (TTM == 4 sütun toplamı,
    fark %0.0000). Tutarlılık doğruluk değildir. Bu modül ikisini ayırır.

Tespit yöntemi (en güvenilirden en zayıfa):

    1. YILLIK TABLO ile karşılaştır (kesin):
         yıllık[Y] ≈ Q4(Y)        -> KÜMÜLATİF
         yıllık[Y] ≈ Σ 4 çeyrek   -> TEKİL
    2. Yıl içi monotonluk (yedek): aynı yılın Q1<Q2<Q3<Q4 artıyorsa kümülatif.
       Yalnızca gelir gibi hiç negatif olmayan kalemlerde kullanılır; net kâr
       zarar yazdığı için monotonluk bozulur -> `assumed` bayrağıyla işaretlenir.

Doğru TTM:

    KÜMÜLATİF:  TTM = FY(son tam yıl) + YTD(güncel) - YTD(geçen yıl aynı dönem)
    TEKİL:      TTM = Σ son 4 çeyrek
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

__all__ = [
    "Mode",
    "TTM",
    "compute_ttm",
    "detect_mode",
    "from_cumulative",
    "from_standalone",
    "naive_borsapy_ttm",
]

Mode = Literal["cumulative", "standalone", "unknown"]

# Yıllık tablo ile çeyreklik tablo arasında eşitlik toleransı (%)
_EQUAL_TOL = 0.005


@dataclass(frozen=True, slots=True)
class TTMPoint:
    """Tek bir çeyrek gözlemi. Kümülatif tabloda `value` = YTD toplamı."""

    year: int
    quarter: int
    value: float

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"


@dataclass(frozen=True, slots=True)
class TTM:
    """TTM sonucu + nasıl elde edildiği (denetlenebilirlik için)."""

    value: float | None
    mode: Mode
    points_used: tuple[str, ...] = ()
    flags: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""

    @property
    def reliable(self) -> bool:
        """`ttm_gap` / `assumed_mode` bayrağı yoksa güvenilir."""
        return self.value is not None and not self.flags


def _sort(points: Iterable[TTMPoint]) -> list[TTMPoint]:
    return sorted(points, key=lambda p: (p.year, p.quarter))


def _close(a: float, b: float, tol: float = _EQUAL_TOL) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale <= tol


def detect_mode(
    points: Sequence[TTMPoint],
    annual: dict[int, float] | None = None,
) -> tuple[Mode, str]:
    """Çeyrek serisinin kümülatif mi tekil mi olduğunu tespit eder.

    `annual`: {2025: yıllık_toplam, ...} — varsa kesin karar verir.
    Dönen ikinci değer: kararın gerekçesi (log/denetim için).
    """
    pts = _sort(points)
    if len(pts) < 2:
        return "unknown", "tek gözlem"

    by_year: dict[int, list[TTMPoint]] = {}
    for p in pts:
        by_year.setdefault(p.year, []).append(p)

    # 1) Yıllık tablo varsa kesin karar
    if annual:
        for year, year_points in by_year.items():
            fy = annual.get(year)
            q4 = next((p for p in year_points if p.quarter == 4), None)
            if fy is None or q4 is None:
                continue
            if _close(q4.value, fy):
                return "cumulative", f"yıllık[{year}] == {q4.label} (kümülatif kanıt)"
            four = [p for p in year_points if p.quarter <= 4]
            if len(four) == 4 and _close(sum(p.value for p in four), fy):
                return "standalone", f"yıllık[{year}] == Σ4 çeyrek (tekil kanıt)"

    # 2) Yıl içi monotonluk (yedek kanıt): tüm yıllarda artan mı?
    same_sign_pairs = 0
    increasing_pairs = 0
    for year_points in by_year.values():
        for a, b in zip(year_points, year_points[1:]):
            if a.quarter + 1 != b.quarter:
                continue
            same_sign_pairs += 1
            if abs(b.value) > abs(a.value):
                increasing_pairs += 1
    if same_sign_pairs >= 2 and increasing_pairs == same_sign_pairs:
        return "cumulative", "yıl içi tüm ardışık çeyrekler artıyor (kümülatif varsayımı)"

    if same_sign_pairs >= 2 and increasing_pairs == 0:
        return "standalone", "yıl içi hiç artış yok (tekil varsayımı)"

    return "unknown", "kanıt yetersiz (yıllık tablo yok, monotonluk tutarsız)"


def from_standalone(points: Sequence[TTMPoint]) -> TTM:
    pts = _sort(points)
    if len(pts) < 4:
        return TTM(None, "standalone", tuple(p.label for p in pts), ("ttm_gap",), "4 çeyrekten az gözlem")
    last4 = pts[-4:]
    gap = any(
        (b.year, b.quarter) != _next_quarter(a.year, a.quarter) for a, b in zip(last4, last4[1:])
    )
    flags = ("ttm_gap",) if gap else ()
    return TTM(
        sum(p.value for p in last4),
        "standalone",
        tuple(p.label for p in last4),
        flags,
        "son 4 çeyrek toplamı" if not gap else "son 4 çeyrek toplamı (çeyrek atlaması var)",
    )


def from_cumulative(points: Sequence[TTMPoint]) -> TTM:
    """TTM = FY(son tam yıl) + YTD(güncel) - YTD(geçen yıl aynı dönem)."""
    pts = _sort(points)
    if not pts:
        return TTM(None, "cumulative", (), ("ttm_gap",), "gözlem yok")

    latest = pts[-1]
    flags: list[str] = []
    used: list[str] = []

    # Son tam yıl: en büyük quarter == 4 olan yıl (güncel yıldan farklı)
    full_years = sorted({p.year for p in pts if p.quarter == 4})
    if not full_years:
        return TTM(None, "cumulative", (latest.label,), ("ttm_gap",), "tam yıl (Q4) yok")
    fy_year = full_years[-1]
    fy = next(p for p in pts if p.year == fy_year and p.quarter == 4)
    used.append(fy.label)

    if latest.quarter == 4:
        # En son veri zaten tam yıl sonu
        return TTM(fy.value, "cumulative", (fy.label,), (), "son veri tam yıl sonu")

    ytd_now = latest
    used.append(ytd_now.label)

    # Geçen yıl aynı dönem
    prev = next((p for p in pts if p.year == ytd_now.year - 1 and p.quarter == ytd_now.quarter), None)
    if prev is None:
        return TTM(
            fy.value,
            "cumulative",
            tuple(used),
            ("ttm_gap", "stale_fundamentals"),
            f"{ytd_now.year - 1}Q{ytd_now.quarter} yok -> son tam yıl kullanıldı",
        )
    used.append(prev.label)
    value = fy.value + ytd_now.value - prev.value
    if ytd_now.value < 0 or prev.value < 0:
        flags.append("ttm_gap")  # zarar dönemi: aritmetik doğru ama yorum zor
    return TTM(
        value,
        "cumulative",
        tuple(used),
        tuple(flags),
        f"{fy.label} + {ytd_now.label} - {prev.label}",
    )


def compute_ttm(
    points: Sequence[TTMPoint],
    annual: dict[int, float] | None = None,
    *,
    monotonic_assumption: bool = True,
) -> TTM:
    """Tek giriş noktası: modu tespit edip doğru TTM'i hesaplar."""
    mode, reason = detect_mode(points, annual)
    if mode == "cumulative":
        result = from_cumulative(points)
    elif mode == "standalone":
        result = from_standalone(points)
    else:
        return TTM(None, "unknown", tuple(p.label for p in _sort(points)), ("ttm_gap",), reason)

    flags = result.flags
    if mode == "cumulative" and "yıllık" not in reason and "varsayımı" in reason and monotonic_assumption:
        flags = flags + ("assumed_mode",)
    return TTM(result.value, result.mode, result.points_used, flags, f"{reason}; {result.note}")


def naive_borsapy_ttm(points: Sequence[TTMPoint]) -> float | None:
    """borsapy'nin yaptığı hesap — SADECE karşılaştırma/uyarı için.

    Üretimde kullanılmaz: kümülatif tabloda 2x hata verir.
    """
    pts = _sort(points)
    if len(pts) < 4:
        return None
    return sum(p.value for p in pts[-4:])


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year, quarter + 1) if quarter < 4 else (year + 1, 1)
