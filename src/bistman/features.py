"""Özellik ve oran motoru — LLM'in yerine geçen hesaplama katmanı.

Tasarım: **LLM aritmetik yapmaz.** Haber tutarı burada, kodda, oranlara çevrilir;
LLM'e hazır oran + bayrak gider (MIMARI.md §4).

Üç oran, tek oran değil:
    ratio_revenue    haber tutarı / TTM ciro        (operasyonel büyüklük)
    ratio_mcap       haber tutarı / piyasa değeri   (yatırımcı açısından büyüklük)
    ratio_net_income haber tutarı / TTM net kâr     (kârlılık etkisi)

Emniyet (`confidence`) **çarpımsal**: iki ayrı sorun varsa ceza katlanır.
Toplamsal olsa "orta" derdi ve yanlış güven üretirdi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Mapping

from .parsing.evidence import event_amount

StatementType = Literal["industrial", "bank"]

# Ceza katsayıları — TASARIM-DETAY.md §3.3
PENALTIES: dict[str, float] = {
    "no_amount": 0.45,
    "fx_fallback": 0.70,
    "ttm_gap": 0.60,
    "mixed_restatement_vintage": 0.60,
    "bank_template_mismatch": 0.50,
    "implausible_ratio": 0.25,
    "stale_fundamentals": 0.85,
    "llm_low_confidence": 0.80,
}

# Oran eşikleri (yüzde). Kalibrasyon Faz 4'te `config/scoring.toml`'a taşınacak.
BUCKETS: tuple[tuple[float, str], ...] = (
    (1.0, "etkisiz"),
    (3.0, "kucuk"),
    (10.0, "orta"),
    (30.0, "buyuk"),
    (float("inf"), "devasa"),
)
PLACEHOLDER_BUCKET_POINTS: dict[str, float] = {
    "etkisiz": 0.0,
    "kucuk": 2.0,
    "orta": 4.0,
    "buyuk": 6.5,
    "devasa": 8.5,
}
IMPLAUSIBLE_RATIO_PCT = 100.0


@dataclass(frozen=True, slots=True)
class Features:
    ticker: str
    amount_try: Decimal | None
    revenue_ttm: Decimal | None
    net_income_ttm: Decimal | None
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    ratio_revenue: float | None = None
    ratio_net_income: float | None = None
    ratio_mcap: float | None = None
    method: str = "unavailable"
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary_ratio(self) -> float | None:
        """Sıralamada kullanılacak baskın oran: PD varsa PD, yoksa ciro."""
        return self.ratio_mcap if self.ratio_mcap is not None else self.ratio_revenue

    def confidence(
        self, base: float = 1.0, penalties: Mapping[str, float] | None = None
    ) -> float:
        return confidence(self.flags, base=base, penalties=penalties)


def _ratio(part: Decimal | None, whole: Decimal | None) -> float | None:
    if part is None or whole is None or not part.is_finite() or not whole.is_finite() or whole <= 0:
        return None
    ratio = float(part / whole * 100)
    return ratio if math.isfinite(ratio) else None


def build_features(
    *,
    ticker: str,
    amount_try: Decimal | None,
    revenue_ttm: Decimal | None,
    net_income_ttm: Decimal | None = None,
    market_cap: Decimal | None = None,
    pe_ratio: Decimal | None = None,
    statement_type: StatementType = "industrial",
    extra_flags: tuple[str, ...] = (),
) -> Features:
    flags: list[str] = list(extra_flags)

    if amount_try is None:
        if "fx_fallback" not in flags:
            flags.append("no_amount")
        return Features(
            ticker=ticker,
            amount_try=None,
            revenue_ttm=revenue_ttm,
            net_income_ttm=net_income_ttm,
            market_cap=market_cap,
            pe_ratio=pe_ratio,
            method="unavailable",
            flags=tuple(dict.fromkeys(flags)),
        )

    ratio_revenue = _ratio(amount_try, revenue_ttm)
    ratio_net_income = _ratio(amount_try, net_income_ttm)
    ratio_mcap = _ratio(amount_try, market_cap)

    if ratio_revenue is None and ratio_mcap is None:
        flags.append("no_denominator")

    if any(r is not None and r > IMPLAUSIBLE_RATIO_PCT for r in (ratio_revenue, ratio_mcap)):
        flags.append("implausible_ratio")

    method = "ttm" if ratio_revenue is not None else "unavailable"
    return Features(
        ticker=ticker,
        amount_try=amount_try,
        revenue_ttm=revenue_ttm,
        net_income_ttm=net_income_ttm,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        ratio_revenue=ratio_revenue,
        ratio_net_income=ratio_net_income,
        ratio_mcap=ratio_mcap,
        method=method,
        flags=tuple(dict.fromkeys(flags)),
    )


def confidence(
    flags: tuple[str, ...] | list[str],
    *,
    base: float = 1.0,
    penalties: Mapping[str, float] | None = None,
) -> float:
    """Bayrakları çarpımsal cezaya çevirir; 0..1 aralığına sıkıştırır.

    `penalties` verilmezse modüldeki varsayılan tablo kullanılır; üretimde
    `config/scoring.toml`'dan gelen tablo enjekte edilir (kod değil ayar).
    """
    table = PENALTIES if penalties is None else penalties
    value = base
    for flag in flags:
        value *= table.get(flag, 1.0)
    return max(0.0, min(1.0, value))


def amount_try_from_text(
    text: str, fx: Mapping[str, Decimal] | None = None
) -> tuple[Decimal | None, tuple[str, ...]]:
    """Metinden TRY cinsinden tutar çıkarır; çıkaramazsa (None, bayraklar).

    `fx_fallback`: tutar bulundu ama kur yok (ör. "500 milyon dolar" ve USD/TRY
    elimizde değil). Uydurma kur yok — bayrak + `None` döneriz.
    """
    return event_amount(text or "").as_try(fx)


def ratio_bucket(ratio_pct: float | None) -> str:
    if ratio_pct is None:
        return "bilinmiyor"
    for limit, label in BUCKETS:
        if ratio_pct < limit:
            return label
    return "devasa"


def bucket_points(ratio_pct: float | None) -> float:
    """Geçici puanlama — Faz 4'te event study sonuçlarıyla değiştirilecek."""
    label = ratio_bucket(ratio_pct)
    return PLACEHOLDER_BUCKET_POINTS.get(label, 0.0)
