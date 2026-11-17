"""Anormal getiri (event study) motoru — 'önemli mi?' sorusunun objektif etiketi.

Neden gerekli: 6 aylık bildirim metni tek başına etiketsiz bir yığındır. Kural
eşiklerini veriyle belirlemek için her bildirime bir "piyasa etkisi" etiketi
gerekiyor. Elimizdeki tek objektif sinyal fiyat tepkisi.

Formül (PLAN-V1 §2.1):

    t0     = yayından SONRAKİ ilk seans (seans içi yayın -> ertesi seans)
    CAR(k) = [kapanış(t0+k) / kapanış(t0-1) - 1] - [endeks aynı pencerede - 1]
    z(k)   = CAR(k) / (σ_günlük × √(k+1))

İki detay hayati:
  * Endeks çıkarılır: yoksa "borsa yükseldiği gün her haber olumlu" çıkar.
  * σ ile normalize edilir: yoksa oynak hisseler sürekli "önemli" görünür.

Dürüstlük notu: bu etiket "önem" değil **piyasa etkisi** ölçer. Haber zaten
fiyatlanmış olabilir. Erken uyarı ürünü için doğru etiket yine budur.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time

from ..parsing.dates import to_trt

# BIST sürekli işlem açılışı. Açık artırma 09:40'ta başlar; t0 sınırı olarak
# 10:00 kullanıyoruz (config'den değiştirilebilir).
SESSION_OPEN_TRT = time(10, 0)
HORIZONS: tuple[int, ...] = (0, 1, 3, 5)
VOL_WINDOW = 60
MIN_VOL_OBS = 20
MATERIAL_Z = 2.0


@dataclass(frozen=True, slots=True)
class Series:
    """Tek enstrümanın günlük kapanışları (tarihe göre artan)."""

    symbol: str
    bars: tuple[tuple[date, float], ...]
    _index: dict[date, float] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if [d for d, _ in self.bars] != sorted(d for d, _ in self.bars):
            raise ValueError(f"{self.symbol}: barlar tarihe göre sıralı olmalı")
        object.__setattr__(self, "_index", {d: c for d, c in self.bars})

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(d for d, _ in self.bars)

    def close(self, day: date) -> float | None:
        return self._index.get(day)


@dataclass(frozen=True, slots=True)
class EventResult:
    ticker: str
    t0: date | None
    car: dict[int, float] = field(default_factory=dict)
    z: dict[int, float] = field(default_factory=dict)
    sigma: float | None = None
    usable: bool = False
    reason: str | None = None

    def abs_z(self, k: int = 1) -> float | None:
        value = self.z.get(k)
        return None if value is None else abs(value)

    def is_material(self, k: int = 1, threshold: float = MATERIAL_Z) -> bool:
        value = self.abs_z(k)
        return value is not None and value >= threshold


def log_returns(series: Series) -> list[float]:
    closes = [c for _d, c in series.bars]
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _t0(published_at: datetime, sessions: tuple[date, ...], open_trt: time) -> date | None:
    """Yayından sonraki ilk seans. Seans içi yayın -> ertesi seans.

    Gerekçe: seans içinde gelen haber o günün kapanışında zaten fiyatlanmış olur;
    baseline'ı bozmamak için ölçümü bir sonraki seanstan başlatırız.
    """
    local = to_trt(published_at)
    day = local.date()
    session_set = set(sessions)

    if day in session_set and local.time() < open_trt:
        return day
    for session in sessions:
        if session > day:
            return session
    return None


def _volatility(series: Series, before: date, window: int) -> float | None:
    """t0'dan ÖNCEKİ `window` seansın günlük oynaklığı (örneklem stdev)."""
    past = tuple((d, c) for d, c in series.bars if d < before)[-window:]
    if len(past) < MIN_VOL_OBS:
        return None
    returns = log_returns(Series(series.symbol, past))
    if len(returns) < MIN_VOL_OBS - 1:
        return None
    try:
        return statistics.stdev(returns)
    except statistics.StatisticsError:
        return None


def compute_event(
    stock: Series,
    market: Series,
    published_at: datetime,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    vol_window: int = VOL_WINDOW,
    open_trt: time = SESSION_OPEN_TRT,
) -> EventResult:
    """Tek bildirim için CAR ve z skorları.

    `market` seans takvimi olarak da kullanılır (XU100 serisi).
    Kullanılamayan durumlar `usable=False` + `reason` ile döner (exception yok:
    tek bozuk olay tüm kalibrasyonu durdurmamalı) ama sebep **her zaman** yazılır.
    """
    sessions = market.dates
    if len(sessions) < 2:
        return EventResult(stock.symbol, None, reason="market_series_too_short")

    t0 = _t0(published_at, sessions, open_trt)
    if t0 is None:
        return EventResult(stock.symbol, None, reason="no_session_after_publish")

    pos = sessions.index(t0)
    if pos == 0:
        return EventResult(stock.symbol, t0, reason="no_previous_session")

    baseline_day = sessions[pos - 1]
    base_stock = stock.close(baseline_day)
    base_market = market.close(baseline_day)
    if base_stock is None or base_market is None:
        return EventResult(stock.symbol, t0, reason="missing_baseline_bar")

    sigma = _volatility(stock, t0, vol_window)

    car: dict[int, float] = {}
    z: dict[int, float] = {}
    for k in horizons:
        target_pos = pos + k
        if target_pos >= len(sessions):
            continue  # gelecek veri yok (henüz gerçekleşmemiş olay)
        target_day = sessions[target_pos]
        end_stock = stock.close(target_day)
        end_market = market.close(target_day)
        if end_stock is None or end_market is None:
            continue

        stock_ret = end_stock / base_stock - 1.0
        market_ret = end_market / base_market - 1.0
        car[k] = stock_ret - market_ret

        if sigma and sigma > 0:
            z[k] = car[k] / (sigma * math.sqrt(k + 1))

    if not car:
        return EventResult(stock.symbol, t0, sigma=sigma, reason="no_target_bar")

    usable = sigma is not None and sigma > 0 and bool(z)
    return EventResult(
        ticker=stock.symbol,
        t0=t0,
        car=car,
        z=z,
        sigma=sigma,
        usable=usable,
        reason=None if usable else "no_volatility",
    )


def window_end(t0: date, sessions: tuple[date, ...], k: int) -> date | None:
    """t0'dan k seans sonrası tarih (raporlama ve testler için)."""
    try:
        pos = sessions.index(t0)
    except ValueError:
        return None
    target = pos + k
    return sessions[target] if 0 <= target < len(sessions) else None
