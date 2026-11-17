"""Event study testleri — tamamı sentetik seri, ağ yok, deterministik."""

from datetime import date, datetime, timedelta

import pytest

from bistman.analysis.event_study import (
    Series,
    compute_event,
    log_returns,
    window_end,
)
from bistman.parsing.dates import TRT


def sessions(start: date, n: int) -> list[date]:
    """Hafta sonu atlayan basit seans takvimi (test için)."""
    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def series(symbol: str, days: list[date], closes: list[float]) -> Series:
    return Series(symbol, tuple(zip(days, closes)))


DAYS = sessions(date(2026, 1, 5), 80)
FLAT = [100.0] * 80


def trt(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TRT)


# ------------------------------------------------------------------ yardımcı


def test_log_returns() -> None:
    s = series("X", DAYS[:3], [100.0, 110.0, 121.0])
    r = log_returns(s)
    assert len(r) == 2
    assert r[1] == pytest.approx(0.0953102, rel=1e-5)


def test_series_requires_sorted() -> None:
    with pytest.raises(ValueError):
        Series("X", ((DAYS[1], 1.0), (DAYS[0], 2.0)))


# ------------------------------------------------------------------ t0 seçimi


def test_publish_before_open_same_day() -> None:
    """09:00'da yayın -> aynı seans t0 (piyasa henüz açılmamış)."""
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[10], 9, 0)
    )
    assert event.t0 == DAYS[10]


def test_publish_during_session_next_day() -> None:
    """14:00'da yayın -> ertesi seans (o günkü hareket baseline'ı kirletmesin)."""
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[10], 14, 0)
    )
    assert event.t0 == DAYS[11]


def test_publish_after_close_next_day() -> None:
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[10], 19, 30)
    )
    assert event.t0 == DAYS[11]


def test_publish_on_weekend_next_session() -> None:
    friday_idx = next(i for i, d in enumerate(DAYS) if d.weekday() == 4)
    friday = DAYS[friday_idx]
    saturday = friday + timedelta(days=1)
    assert saturday.weekday() == 5
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(saturday, 12, 0)
    )
    assert event.t0 == DAYS[friday_idx + 1]  # pazartesi


# ------------------------------------------------------------------ CAR / z


def test_car_removes_market_move() -> None:
    """Hisse +2%, endeks +0.5% -> CAR %1.5 (endeks çıkarılmış)."""
    closes_stock = [100.0] * 11 + [102.0] + [102.0] * 10
    closes_market = [100.0] * 11 + [100.5] + [100.5] * 10
    event = compute_event(
        series("X", DAYS, closes_stock),
        series("XU100", DAYS, closes_market),
        trt(DAYS[11], 9, 0),  # t0 = DAYS[11], baseline = DAYS[10]
        horizons=(0,),
    )
    assert event.car[0] == pytest.approx(0.015, rel=1e-6)


def test_flat_case_is_zero() -> None:
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[11], 9, 0)
    )
    assert event.car[1] == pytest.approx(0.0, abs=1e-12)


def test_z_division_guard_zero_volatility() -> None:
    """Sabit seride sigma=0 → z üretilmez, sebep yazılır (sıfıra bölme yok)."""
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[40], 9, 0)
    )
    assert event.sigma == pytest.approx(0.0)
    assert event.z == {}
    assert event.usable is False
    assert event.reason == "no_volatility"


def test_z_normalised_by_volatility() -> None:
    """Aynı CAR, iki farklı oynaklıkta iki farklı z üretmeli."""
    # oynak hisse: ±%1 salınım
    volatile = [100.0 + (1.0 if i % 2 else -1.0) for i in range(80)]
    calm = [100.0 + (0.2 if i % 2 else -0.2) for i in range(80)]
    for closes in (volatile, calm):
        closes[61] = closes[60] * 1.02  # t0 = DAYS[61], baseline DAYS[60]
    ev_vol = compute_event(
        series("V", DAYS, volatile), series("XU100", DAYS, FLAT), trt(DAYS[61], 9, 0),
        horizons=(0,),
    )
    ev_calm = compute_event(
        series("C", DAYS, calm), series("XU100", DAYS, FLAT), trt(DAYS[61], 9, 0),
        horizons=(0,),
    )
    assert ev_vol.z[0] is not None and ev_calm.z[0] is not None
    assert abs(ev_calm.z[0]) > abs(ev_vol.z[0])  # düşük oynaklık -> daha yüksek z


def test_insufficient_history_for_volatility() -> None:
    short_days = DAYS[:5]
    event = compute_event(
        series("X", short_days, [100.0] * 5),
        series("XU100", short_days, [100.0] * 5),
        trt(short_days[2], 9, 0),
    )
    assert event.sigma is None
    assert event.usable is False


def test_missing_bar_reported_not_crashed() -> None:
    """Askıya alınmış hisse: bar yoksa olay kullanılamaz, sebep yazılır."""
    stock_days = [d for d in DAYS if d != DAYS[12]]
    stock_closes = [100.0] * len(stock_days)
    event = compute_event(
        series("X", stock_days, stock_closes),
        series("XU100", DAYS, FLAT),
        trt(DAYS[12], 9, 0),
    )
    assert event.usable is False or 0 not in event.car


def test_future_horizon_is_skipped() -> None:
    """Olay bugüne çok yakınsa ileri ufuklar hesaplanamaz — eksik kalır, patlamaz."""
    event = compute_event(
        series("X", DAYS, FLAT), series("XU100", DAYS, FLAT), trt(DAYS[-1], 9, 0)
    )
    assert event.t0 == DAYS[-1]
    assert 5 not in event.car


def test_is_material_threshold() -> None:
    # t0 öncesi oynaklık olmalı (yoksa sigma=0, z üretilmez), sonra %4 sıçrama
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(80)]
    closes[61] = closes[60] * 1.04
    event = compute_event(
        series("X", DAYS, closes), series("XU100", DAYS, FLAT), trt(DAYS[61], 9, 0)
    )
    assert event.abs_z(0) is not None
    assert event.is_material(0, threshold=1.0) is True


def test_window_end_helper() -> None:
    assert window_end(DAYS[10], tuple(DAYS), 1) == DAYS[11]
    assert window_end(DAYS[-1], tuple(DAYS), 1) is None
