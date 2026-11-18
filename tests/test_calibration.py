"""Kalibrasyon testleri — 'günde N bildirimde isabet oranı' tablosunun doğruluğu."""

from datetime import date

import pytest

from bistman.analysis.calibration import ScoredEvent, evaluate, select_pushes, sweep

D1 = date(2026, 3, 2)
D2 = date(2026, 3, 3)


def ev(ticker: str, day: date, score: float, z1: float | None, car1: float | None = None):
    return ScoredEvent(ticker=ticker, t0=day, score=score, z1=z1, car1=car1)


EVENTS = [
    ev("A", D1, 8.0, 2.5, 0.04),   # material, yüksek skor
    ev("B", D1, 7.0, 0.3, 0.002),  # material değil
    ev("C", D1, 6.0, -2.2, -0.03), # material (negatif yön de sayılır)
    ev("D", D1, 4.0, 3.0, 0.05),   # material ama eşik altı -> kaçırılan
    ev("E", D2, 7.5, 0.1, 0.0),    # material değil
]


def test_material_respects_both_directions() -> None:
    assert ev("X", D1, 5, 2.5).material is True
    assert ev("X", D1, 5, -2.5).material is True
    assert ev("X", D1, 5, 1.99).material is False
    assert ev("X", D1, 5, None).material is False


def test_threshold_filters_and_precision() -> None:
    report = evaluate(EVENTS, threshold=5.5)
    # A(8.0), B(7.0), C(6.0), E(7.5) geçer; D(4.0) eşik altı. Material: A ve C.
    assert report.pushed == 4
    assert report.hits == 2
    assert report.precision == pytest.approx(0.5)
    assert report.days == 2
    assert report.missed_material == 1  # D


def test_daily_budget_keeps_best_scores() -> None:
    pushed = select_pushes(EVENTS, threshold=0.0, max_per_day=2)
    day1 = [e for e in pushed if e.t0 == D1]
    assert len(day1) == 2
    assert [e.ticker for e in day1] == ["A", "B"]  # 8.0 ve 7.0 kazandı
    assert len([e for e in pushed if e.t0 == D2]) == 1


def test_budget_reduces_pushes_but_counts_missed() -> None:
    loose = evaluate(EVENTS, threshold=0.0)
    tight = evaluate(EVENTS, threshold=0.0, max_per_day=1)
    assert tight.pushed < loose.pushed
    assert tight.missed_material > loose.missed_material


def test_avg_abs_car_only_over_pushed() -> None:
    report = evaluate(EVENTS, threshold=5.5)
    cars = [abs(e.car1) for e in EVENTS if e.score >= 5.5 and e.car1 is not None]
    assert report.avg_abs_car == pytest.approx(sum(cars) / len(cars))


def test_empty_input() -> None:
    report = evaluate([], threshold=6.5)
    assert (report.pushed, report.hits, report.days) == (0, 0, 0)
    assert report.precision == 0.0
    assert report.avg_abs_car is None


def test_sweep_grid_size_and_rows_render() -> None:
    reports = sweep(EVENTS, thresholds=(4.0, 6.5, 8.0), max_per_day=(3, None))
    assert len(reports) == 6
    for r in reports:
        row = r.as_row()
        assert "isabet" in row and "kaçan" in row


def test_monotonicity_of_threshold() -> None:
    """Eşik yükseldikçe push sayısı artmamalı."""
    pushes = [evaluate(EVENTS, threshold=t).pushed for t in (3.0, 5.0, 7.0, 9.0)]
    assert pushes == sorted(pushes, reverse=True)
