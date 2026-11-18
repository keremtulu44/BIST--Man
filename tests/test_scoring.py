"""Kural skor motoru testleri — eşikler config'den, davranış test altında."""

from decimal import Decimal

import pytest

from bistman.features import build_features
from bistman.scoring import ScoringConfig, match_subject, score

D = Decimal
CFG = ScoringConfig.load()


def features_with(
    amount: str | None = "60000000000", revenue: str | None = "500000000000", **kw
):
    return build_features(
        ticker="THYAO",
        amount_try=D(amount) if amount else None,
        revenue_ttm=D(revenue) if revenue else None,
        **kw,
    )


# --------------------------------------------------------------- konu eşleme


@pytest.mark.parametrize(
    ("subject", "expected_key"),
    [
        ("Yeni Sözleşme İmzalanması", "sözleşme"),
        ("Haber ve Söylentilere İlişkin Açıklama", "haber ve söylenti"),
        ("Bedelli Sermaye Artırımı", "bedelli"),
        ("Bedelsiz Sermaye Artırımı", "bedelsiz"),
        ("Kâr Dağıtımına İlişkin Yönetim Kurulu Kararı", "kâr dağıtım"),
        ("Bilinmeyen Bir Konu", "varsayılan"),
        ("", "varsayılan"),
    ],
)
def test_match_subject(subject: str, expected_key: str) -> None:
    key, _factor = match_subject(subject, CFG.subject_prior)
    assert key == expected_key


def test_match_subject_prefers_longest_key() -> None:
    """'yatırım kararı' varken sadece 'yatırım' eşleşmesin."""
    priors = {"yatırım": 1.0, "yatırım kararı": 2.0}
    key, factor = match_subject("Yeni Yatırım Kararı Açıklaması", priors)
    assert (key, factor) == ("yatırım kararı", 2.0)


# --------------------------------------------------------------------- skor


def test_big_contract_is_push() -> None:
    """60 milyar TL / 500 milyar TL = %12 → 'buyuk' + sözleşme → push."""
    decision = score(features_with(), "Yeni Sözleşme İmzalanması", CFG)
    assert decision.action == "push"
    assert decision.impact == pytest.approx(9.0)  # 6.5 + 2.5
    assert decision.push_score >= CFG.push_threshold


def test_small_contract_is_not_push() -> None:
    decision = score(
        features_with(amount="12000000000"), "Yeni Sözleşme İmzalanması", CFG
    )  # %2.4 -> 'kucuk' (2.0) + 2.5 = 4.5
    assert decision.action == "digest"
    assert decision.impact == pytest.approx(4.5)


def test_negative_prior_can_floor_at_zero() -> None:
    decision = score(features_with(amount="100"), "Ceza Kesilmesi", CFG)
    assert decision.impact >= 0.0


def test_no_amount_collapses_confidence() -> None:
    decision = score(features_with(amount=None), "Yeni Sözleşme İmzalanması", CFG)
    assert decision.confidence == pytest.approx(0.45)
    assert decision.action in {"digest", "silent"}


def test_uncertain_but_big_is_notified_with_label() -> None:
    """Büyük oran + implausible cezası: emniyet düşük, impact yüksek -> etiketli push."""
    features = build_features(
        ticker="THYAO", amount_try=D("60000000000"), revenue_ttm=D("1000000000")  # %6000
    )
    assert "implausible_ratio" in features.flags
    decision = score(features, "Yeni Sözleşme İmzalanması", CFG)
    assert decision.action == "push_uncertain"
    assert decision.should_notify is True
    assert any("belirsiz ama büyük" in r for r in decision.reasons)


def test_reasons_are_human_readable() -> None:
    decision = score(features_with(), "Yeni Sözleşme İmzalanması", CFG)
    joined = " | ".join(decision.reasons)
    assert "konu: sözleşme" in joined
    assert "oran: %12.00" in joined
    assert "emniyet:" in joined


def test_config_is_data_not_code() -> None:
    """Eşiği config'den değiştirmek davranışı değiştirmeli (deploy gerekmez)."""
    strict = ScoringConfig(
        subject_prior=CFG.subject_prior,
        bucket_points=CFG.bucket_points,
        penalties=CFG.penalties,
        push_threshold=9.5,
        digest_threshold=CFG.digest_threshold,
        max_push_per_ticker_per_day=CFG.max_push_per_ticker_per_day,
        uncertain_confidence_below=CFG.uncertain_confidence_below,
        uncertain_min_impact=CFG.uncertain_min_impact,
    )
    features = features_with()
    assert score(features, "Yeni Sözleşme İmzalanması", CFG).action == "push"
    assert score(features, "Yeni Sözleşme İmzalanması", strict).action == "digest"


def test_penalties_come_from_config() -> None:
    harsh = ScoringConfig(
        subject_prior=CFG.subject_prior,
        bucket_points=CFG.bucket_points,
        penalties={**CFG.penalties, "no_amount": 0.1},
        push_threshold=CFG.push_threshold,
        digest_threshold=CFG.digest_threshold,
        max_push_per_ticker_per_day=CFG.max_push_per_ticker_per_day,
        uncertain_confidence_below=CFG.uncertain_confidence_below,
        uncertain_min_impact=CFG.uncertain_min_impact,
    )
    decision = score(features_with(amount=None), "Yeni Sözleşme İmzalanması", harsh)
    assert decision.confidence == pytest.approx(0.1)
