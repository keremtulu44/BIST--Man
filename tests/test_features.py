"""Oran/ceza motoru testleri — 'sessiz yanlış' burada önlenir."""

from decimal import Decimal

import pytest

from bistman.features import (
    Features,
    build_features,
    bucket_points,
    confidence,
    ratio_bucket,
)

D = Decimal


def test_ratio_revenue_classic_example() -> None:
    """15 milyar TL haber / 500 milyar TL ciro = %3."""
    f = build_features(
        ticker="THYAO",
        amount_try=D("15000000000"),
        revenue_ttm=D("500000000000"),
        market_cap=D("200000000000"),
    )
    assert f.ratio_revenue == pytest.approx(3.0)
    assert f.ratio_mcap == pytest.approx(7.5)
    assert f.method == "ttm"
    assert f.flags == ()
    assert f.confidence() == pytest.approx(1.0)


def test_no_amount_is_null_not_zero() -> None:
    """`0` yazmak modelin 'etkisiz haber' demesine yol açar; None doğru sinyal."""
    f = build_features(ticker="X", amount_try=None, revenue_ttm=D("1000"))
    assert f.amount_try is None
    assert f.ratio_revenue is None
    assert f.method == "unavailable"
    assert "no_amount" in f.flags
    assert f.confidence() == pytest.approx(0.45)


def test_zero_or_negative_revenue_does_not_crash() -> None:
    for revenue in (D("0"), D("-500")):
        f = build_features(ticker="X", amount_try=D("100"), revenue_ttm=revenue)
        assert f.ratio_revenue is None
        assert "no_denominator" in f.flags


def test_implausible_ratio_flagged_and_penalised() -> None:
    """Oran %100'ü aşıyorsa parser/veri hatası olabilir — skor güvenilmemeli."""
    f = build_features(
        ticker="X", amount_try=D("5000"), revenue_ttm=D("1000")  # %500
    )
    assert f.ratio_revenue == pytest.approx(500.0)
    assert "implausible_ratio" in f.flags
    assert f.confidence() == pytest.approx(0.25)


def test_confidence_is_multiplicative() -> None:
    assert confidence([]) == 1.0
    assert confidence(["fx_fallback"]) == pytest.approx(0.70)
    assert confidence(["fx_fallback", "ttm_gap"]) == pytest.approx(0.42)
    assert confidence(["fx_fallback", "ttm_gap", "implausible_ratio"]) < 0.15


def test_unknown_flag_has_no_effect() -> None:
    assert confidence(["bilinmeyen_bayrak"]) == 1.0


def test_pe_ratio_passes_through_untouched() -> None:
    """F/K hesabı bize ait değil; sağlayıcıdan geldiyse source ile saklanır."""
    f = build_features(
        ticker="AKBNK",
        amount_try=D("1000"),
        revenue_ttm=D("100000"),
        pe_ratio=D("4.2"),
        statement_type="bank",
    )
    assert f.pe_ratio == D("4.2")


def test_bank_mismatch_flag() -> None:
    f = build_features(
        ticker="AKBNK",
        amount_try=D("1000"),
        revenue_ttm=D("100000"),
        statement_type="bank",
        extra_flags=("bank_template_mismatch",),
    )
    assert "bank_template_mismatch" in f.flags
    assert f.confidence() == pytest.approx(0.5)


def test_primary_ratio_prefers_market_cap() -> None:
    f = build_features(
        ticker="X",
        amount_try=D("1000"),
        revenue_ttm=D("100000"),      # %1
        market_cap=D("4000"),         # %25
    )
    assert f.primary_ratio == pytest.approx(25.0)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(None, "bilinmiyor"), (0.5, "etkisiz"), (2.0, "kucuk"), (5.0, "orta"),
     (20.0, "buyuk"), (99.0, "devasa"), (10.0, "buyuk")],
)
def test_buckets(ratio, expected) -> None:
    assert ratio_bucket(ratio) == expected


def test_bucket_points_monotonic() -> None:
    points = [bucket_points(r) for r in (0.5, 2.0, 5.0, 20.0, 99.0)]
    assert points == sorted(points)
    assert bucket_points(None) == 0.0


def test_features_is_frozen() -> None:
    f = build_features(ticker="X", amount_try=D("1"), revenue_ttm=D("100"))
    assert isinstance(f, Features)
    with pytest.raises(Exception):
        f.ticker = "Y"  # type: ignore[misc]
