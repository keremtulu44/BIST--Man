"""Eşik kalibrasyonu — 'günde 3 bildirimde isabet oranı' hesabı.

PLAN-V1 §2.2: istatistiksel anlamlılık KULLANILMAZ (3.000 olayda %5'i şans eseri
'anlamlı' çıkar). Ürün sorusuna çeviririz:

    "Günde en fazla N bildirim göndereceksem, bunların kaçı |z| >= 2 çıkıyor?"

Bu modül o tabloyu doldurur: kural varyantı -> push/gün, isabet, ortalama |CAR|,
kaçırılan büyük olay.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

MATERIAL_Z = 2.0


@dataclass(frozen=True, slots=True)
class ScoredEvent:
    """Kural motorunun ürettiği skor + event study'nin ürettiği etiket."""

    ticker: str
    t0: date
    score: float
    """push_score = impact × confidence (kural motoru)."""
    z1: float | None = None
    """1 seanslık anormal getirinin z skoru (event study)."""
    car1: float | None = None

    @property
    def material(self) -> bool:
        return self.z1 is not None and abs(self.z1) >= MATERIAL_Z


@dataclass(frozen=True, slots=True)
class VariantReport:
    name: str
    threshold: float
    max_per_day: int | None
    pushed: int
    hits: int
    missed_material: int
    days: int
    avg_abs_car: float | None
    precision: float

    def as_row(self) -> str:
        prec = f"{self.precision:.0%}" if self.pushed else "—"
        car = f"{self.avg_abs_car:.2%}" if self.avg_abs_car is not None else "—"
        budget = self.max_per_day if self.max_per_day is not None else "∞"
        per_day = self.pushed / self.days if self.days else 0.0
        return (
            f"{self.name:<28} eşik={self.threshold:<4.1f} bütçe={budget:<3} "
            f"push/gün={per_day:>5.2f} isabet={prec:>5} ort|CAR|={car:>7} "
            f"kaçan={self.missed_material}"
        )


def select_pushes(
    events: Iterable[ScoredEvent],
    *,
    threshold: float,
    max_per_day: int | None = None,
) -> list[ScoredEvent]:
    """Eşiği geçenleri gün bazında bütçeye göre kırpar (en yüksek skor kazanır)."""
    by_day: dict[date, list[ScoredEvent]] = defaultdict(list)
    for event in events:
        if event.score >= threshold:
            by_day[event.t0].append(event)

    selected: list[ScoredEvent] = []
    for day_events in by_day.values():
        day_events.sort(key=lambda e: (-e.score, e.ticker))
        selected.extend(day_events[:max_per_day] if max_per_day else day_events)
    selected.sort(key=lambda e: (e.t0, -e.score))
    return selected


def evaluate(
    events: Sequence[ScoredEvent],
    *,
    threshold: float,
    max_per_day: int | None = None,
    name: str = "varyant",
) -> VariantReport:
    pushed = select_pushes(events, threshold=threshold, max_per_day=max_per_day)
    pushed_ids = {id(e) for e in pushed}

    hits = sum(1 for e in pushed if e.material)
    missed = sum(1 for e in events if e.material and id(e) not in pushed_ids)
    cars = [abs(e.car1) for e in pushed if e.car1 is not None]
    days = len({e.t0 for e in events})

    return VariantReport(
        name=name,
        threshold=threshold,
        max_per_day=max_per_day,
        pushed=len(pushed),
        hits=hits,
        missed_material=missed,
        days=days,
        avg_abs_car=(sum(cars) / len(cars)) if cars else None,
        precision=(hits / len(pushed)) if pushed else 0.0,
    )


def sweep(
    events: Sequence[ScoredEvent],
    *,
    thresholds: Sequence[float] = (3.5, 5.0, 6.5, 7.5),
    max_per_day: Sequence[int | None] = (3, None),
    name: str = "varyant",
) -> list[VariantReport]:
    """Eşik × bütçe ızgarası — PLAN-V1 §2.2 tablosunun satırları."""
    return [
        evaluate(events, threshold=t, max_per_day=b, name=name)
        for t in thresholds
        for b in max_per_day
    ]
