"""TTM modu tespiti — GERÇEK doğrulama sayılarıyla (17.11.2026, THYAO + AKBNK).

Bu dosya bir bulguyu kilitler: İş Yatırım'ın çeyreklik tablosu KÜMÜLATİF,
borsapy'nin TTM'i ise 4 sütunu topluyor -> 2x hata. Sayılar kullanıcının
kendi makinesinde alınmış gerçek raporundan (`docs/dogrulama-sonuc.json`).
"""

from __future__ import annotations

import pytest

from bistman.analysis.ttm import (
    TTMPoint,
    compute_ttm,
    detect_mode,
    from_cumulative,
    from_standalone,
    naive_borsapy_ttm,
)

# THYAO "Satış Gelirleri", milyar TL (kümülatif YTD)
THYAO_REV = [
    TTMPoint(2025, 3, 690.825),
    TTMPoint(2025, 4, 1187.816),
    TTMPoint(2026, 1, 257.961),
    TTMPoint(2026, 2, 585.069),
]
THYAO_ANNUAL = {2025: 1187.816, 2024: 1043.0}

# AKBNK "1.7 Diğer Faiz Gelirleri", milyar TL (kümülatif YTD)
AKBNK_REV = [
    TTMPoint(2025, 3, 0.974),
    TTMPoint(2025, 4, 1.83876),
    TTMPoint(2026, 1, 0.455432),
    TTMPoint(2026, 2, 1.004),
]
AKBNK_ANNUAL = {2025: 1.83876, 2024: 1.5}


def test_borsapy_naive_sum_matches_report() -> None:
    """borsapy TTM = 4 sütun toplamı; rapordaki 2,721,671,196,737 ile birebir."""
    assert naive_borsapy_ttm(THYAO_REV) * 1e9 == pytest.approx(2_721_671_000_000, rel=1e-6)


def test_detect_cumulative_from_annual_table() -> None:
    """Kesin kanıt: yıllık[2025] == 2025Q4 -> tablo kümülatif."""
    mode, reason = detect_mode(THYAO_REV, THYAO_ANNUAL)
    assert mode == "cumulative"
    assert "kümülatif kanıt" in reason


def test_detect_standalone_from_annual_table() -> None:
    """Tekil olsaydı: yıllık[Y] == 4 çeyreğin toplamı."""
    standalone = [
        TTMPoint(2025, 1, 100.0),
        TTMPoint(2025, 2, 120.0),
        TTMPoint(2025, 3, 90.0),
        TTMPoint(2025, 4, 110.0),
    ]
    mode, reason = detect_mode(standalone, {2025: 420.0})
    assert mode == "standalone"
    assert "tekil kanıt" in reason


def test_detect_cumulative_without_annual_by_monotonicity() -> None:
    """Yıllık tablo yoksa yıl içi monotonluk yedek kanıt."""
    mode, _ = detect_mode(THYAO_REV)
    assert mode == "cumulative"


def test_detect_unknown_when_evidence_is_weak() -> None:
    points = [TTMPoint(2025, 1, 100.0), TTMPoint(2025, 2, 80.0), TTMPoint(2026, 1, 60.0)]
    mode, _ = detect_mode(points)
    assert mode == "unknown"


def test_cumulative_ttm_arithmetic() -> None:
    """TTM = FY + YTD(güncel) - YTD(geçen yıl aynı dönem).

    Gerçek 2025Q2 değeri bilinmiyor; 6 aylık YTD'nin 9 aylığın %55'i olduğu
    varsayımıyla ~380 milyar alındı (THYAO için makul).
    """
    points = THYAO_REV + [TTMPoint(2025, 2, 380.0)]
    result = from_cumulative(points)
    assert result.value == pytest.approx(1187.816 + 585.069 - 380.0)
    assert result.points_used == ("2025Q4", "2026Q2", "2025Q2")


def test_cumulative_ttm_is_far_below_naive_sum() -> None:
    """Asıl bulgu: doğru hesap borsapy'nin yarısı. 2x hata oranı oranlara 2x yansır."""
    points = THYAO_REV + [TTMPoint(2025, 2, 380.0)]
    correct = from_cumulative(points).value
    naive = naive_borsapy_ttm(points)
    assert correct is not None and naive is not None
    assert naive / correct > 1.9  # ~2x fazla sayım


def test_cumulative_without_previous_year_falls_back_with_flags() -> None:
    """Geçen yılın aynı dönemi yoksa son tam yıla düşer ama bayrak bırakır."""
    result = from_cumulative(THYAO_REV)  # 2025Q2 yok
    assert result.value == pytest.approx(1187.816)
    assert "ttm_gap" in result.flags
    assert "stale_fundamentals" in result.flags
    assert result.reliable is False


def test_latest_is_year_end_needs_no_arithmetic() -> None:
    points = [TTMPoint(2024, 4, 900.0), TTMPoint(2025, 4, 1187.816)]
    result = from_cumulative(points)
    assert result.value == pytest.approx(1187.816)
    assert result.flags == ()


def test_standalone_needs_four_consecutive_quarters() -> None:
    points = [
        TTMPoint(2025, 1, 100.0),
        TTMPoint(2025, 2, 110.0),
        TTMPoint(2025, 3, 120.0),
        TTMPoint(2026, 1, 130.0),  # 2025Q4 atlanmış
    ]
    result = from_standalone(points)
    assert "ttm_gap" in result.flags


def test_standalone_ok_with_four_consecutive() -> None:
    points = [TTMPoint(2025, q, 100.0 + q) for q in (1, 2, 3, 4)]
    result = from_standalone(points)
    assert result.value == pytest.approx(410.0)
    assert result.flags == ()


def test_compute_ttm_end_to_end_uses_correct_branch() -> None:
    points = THYAO_REV + [TTMPoint(2025, 2, 380.0)]
    result = compute_ttm(points, THYAO_ANNUAL)
    assert result.mode == "cumulative"
    assert result.value == pytest.approx(1392.885, rel=1e-6)
    assert "assumed_mode" not in result.flags  # yıllık tablo kanıtı var


def test_assumed_flag_when_only_monotonicity_supports_cumulative() -> None:
    """Kanıt zayıfsa sonuç kullanılır ama `assumed_mode` ile işaretlenir."""
    points = THYAO_REV + [TTMPoint(2025, 2, 380.0)]
    result = compute_ttm(points, annual=None)
    assert result.mode == "cumulative"
    assert "assumed_mode" in result.flags


def test_unknown_mode_returns_no_value() -> None:
    points = [TTMPoint(2026, 1, 50.0)]
    result = compute_ttm(points, annual=None)
    assert result.value is None
    assert "ttm_gap" in result.flags


def test_bank_template_uses_same_arithmetic() -> None:
    """Banka şablonunda da kümülatif; tarih aritmetiği şablondan bağımsız."""
    mode, _ = detect_mode(AKBNK_REV, AKBNK_ANNUAL)
    assert mode == "cumulative"
    points = AKBNK_REV + [TTMPoint(2025, 2, 0.78)]
    correct = from_cumulative(points).value
    naive = naive_borsapy_ttm(points)
    assert correct is not None and naive is not None
    assert naive > correct


def test_missing_prior_full_year_never_mixes_older_fy_silently() -> None:
    """2025Q4 eksikken 2024Q4 + 2026Q2 - 2025Q2 güvenilir TTM sayılamaz."""
    points = [TTMPoint(2024, 4, 1000.0), TTMPoint(2025, 2, 600.0), TTMPoint(2026, 2, 800.0)]
    result = compute_ttm(points, annual={2024: 1000.0})
    assert "ttm_gap" in result.flags and "stale_fundamentals" in result.flags
    assert result.reliable is False


def test_year_end_preserves_zero_data_gap_flag() -> None:
    points = [TTMPoint(2025, 1, 200.0), TTMPoint(2025, 2, 0.0), TTMPoint(2025, 4, 1000.0)]
    result = from_cumulative(points)
    assert result.value == pytest.approx(1000.0)
    assert "data_gap" in result.flags
