"""Kalibrasyon tablosu demosu (PLAN-V1 §2.2).

Gerçek veri gelene kadar tablo şeklini ve karar mantığını gösterir.
Sentetik olaylar üretir: skor ile |z| arasında bilinçli bir korelasyon var,
böylece "iyi kural" ile "şans" arasındaki farkı tabloda görebilirsin.

Gerçek çalıştırma (Faz 4): KAP geçmişi + fiyat serisi -> ScoredEvent listesi
-> `sweep()`. Kod aynı, sadece veri gerçek olur.

Kullanım:
    .venv/bin/python scripts/kalibrasyon_tablosu.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bistman.analysis.calibration import ScoredEvent, sweep  # noqa: E402


def synthetic_events(n_days: int = 120, per_day: float = 4.0, seed: int = 7) -> list[ScoredEvent]:
    """6 aylık ~480 bildirim benzeri olay.

    Skor ile gerçek etki arasında korelasyon kuruyoruz (gerçek hayatta olması
    umulan şey): yüksek skorlu haberlerin |z| değeri ortalama daha yüksek.
    """
    rng = random.Random(seed)
    start = date(2026, 1, 5)
    events: list[ScoredEvent] = []

    day = start
    for _ in range(n_days):
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        count = max(0, int(rng.gauss(per_day, 1.5)))
        for _ in range(count):
            # skor 0-10, çoğu düşük (gerçek akış böyle)
            score = min(10.0, max(0.0, rng.gauss(4.0, 2.0)))
            signal = (score / 10.0) * 2.2           # skorun açıkladığı kısım
            noise = rng.gauss(0.0, 1.6)             # açıklamadığı kısım
            z1 = signal + noise
            car1 = z1 * 0.011 + rng.gauss(0, 0.004)
            events.append(
                ScoredEvent(ticker=f"T{rng.randint(1, 50):02d}", t0=day, score=score, z1=z1, car1=car1)
            )
        day += timedelta(days=1)

    return events


def main() -> int:
    events = synthetic_events()
    material = sum(1 for e in events if e.material)
    days = len({e.t0 for e in events})
    print(f"{len(events)} olay / {days} seans | gerçekten 'material' (|z|>=2): {material} "
          f"({material / len(events):.1%})\n")

    print("KURAL VARYANTLARI (sentetik veri — gerçek kalibrasyon Faz 4'te)\n")
    reports = sweep(
        events,
        thresholds=(3.5, 5.0, 6.5, 7.5),
        max_per_day=(3, None),
        name="kural",
    )
    for report in reports:
        print(report.as_row())

    print("\nOkuma:")
    print("  push/gün  : günde kaç bildirim gönderilir")
    print("  isabet    : gönderilenlerin kaçı gerçekten |z|>=2")
    print("  kaçan     : eşiği geçemediği için kaçırılan 'material' olay sayısı")
    print("  karar     : kaçan'ı kabul edilebilir tutarken isabet'i maksimize eden satır")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
