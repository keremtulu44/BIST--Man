"""Fiyat geçmişi — event study'nin girdisi. **Standart kütüphane**, anahtar yok.

Neden iki kaynak: elimizde doğrulanmış tek şey İş Yatırım'ın **endeks** geçmişi
(`ChartData.aspx/IndexHistoricalAll`). Hisse kapanışları için aynı uç yok; bu
yüzden hisse fiyatı Yahoo Finance'in kamuya açık chart ucundan alınır
(`THYAO.IS`, anahtar/hesap gerekmez). İkisi de salt okuma ve kişisel kullanım
kapsamında.

Tasarım kararları:

* **Saat dilimi sabit +03:00.** BIST kapanışı 18:10 TRT = 15:10 UTC, aynı gün.
  `zoneinfo` Windows'ta IANA veritabanı olmadan çalışmaz (`tzdata` paketi
  gerekir); Türkiye 2016'dan beri yaz saati uygulamadığı için sabit ofset
  hem doğru hem bağımlılıksız. Gün sınırı UTC'de kaymaz (15:10 UTC).
* **Boşluk doldurulmaz.** Eksik seans tahminle kapatılmaz; `Series` yalnız
  gerçek kapanışları taşır ve event study eksik barı `reason` ile raporlar.
* **Disk önbelleği** (`data/prices/*.json`): 400 günlük seriyi her kalibrasyonda
  yeniden indirmek hem yavaş hem kaba olur. Dosya varsa ve tazeyse okunur.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..analysis.event_study import Series

__all__ = [
    "PriceError",
    "PriceSource",
    "fetch_series",
    "parse_isyatirim_index",
    "parse_yahoo_chart",
]

TRT = timezone(timedelta(hours=3))  # Türkiye: 2016'dan beri sabit UTC+3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

ISYATIRIM_INDEX_URL = (
    "https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common"
    "/ChartData.aspx/IndexHistoricalAll"
)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class PriceError(RuntimeError):
    """Fiyat serisi alınamadı veya beklenen biçimde değil."""


@dataclass(frozen=True, slots=True)
class PriceSource:
    """Hangi kaynağın kullanılacağı. `index` -> İş Yatırım, `stock` -> Yahoo."""

    kind: str
    """'index' | 'stock'"""
    suffix: str = ".IS"


# --------------------------------------------------------------- ayrıştırıcılar


def _bars_to_series(symbol: str, bars: list[tuple[date, float]]) -> Series:
    unique: dict[date, float] = {}
    for day, close in bars:
        if close is None or close <= 0:
            continue
        unique[day] = float(close)
    if len(unique) < 2:
        raise PriceError(f"{symbol}: yeterli kapanış yok ({len(unique)} bar)")
    return Series(symbol, tuple(sorted(unique.items())))


def parse_yahoo_chart(payload: dict, symbol: str) -> Series:
    """Yahoo `v8/finance/chart` yanıtı -> `Series`.

    Biçim: chart.result[0].{timestamp[], indicators.quote[0].close[]}
    """
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise PriceError(f"{symbol}: Yahoo boş yanıt ({error})")
    entry = result[0]
    timestamps = entry.get("timestamp") or []
    quote = ((entry.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []

    bars: list[tuple[date, float]] = []
    for stamp, close in zip(timestamps, closes):
        if close is None:
            continue  # işlem görmemiş / tatil günü
        moment = datetime.fromtimestamp(int(stamp), tz=timezone.utc).astimezone(TRT)
        bars.append((moment.date(), float(close)))
    return _bars_to_series(symbol, bars)


def parse_isyatirim_index(payload: Any, symbol: str) -> Series:
    """İş Yatırım `IndexHistoricalAll` yanıtı -> `Series`.

    Biçim: {"data": [[timestamp_ms, deger], ...]} (uç günlük tek değer verir;
    OHLC yoktur, kapanış = değer).
    """
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not rows:
        raise PriceError(f"{symbol}: İş Yatırım endeks verisi boş")

    bars: list[tuple[date, float]] = []
    for item in rows:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                stamp = float(item[0])
                value = float(item[1])
                if not stamp or value <= 0:
                    continue
                bars.append((datetime.fromtimestamp(stamp / 1000, tz=TRT).date(), value))
        except (TypeError, ValueError):
            continue
    return _bars_to_series(symbol, bars)


# --------------------------------------------------------------------- istemci


def _read(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise PriceError(f"HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise PriceError(f"bağlantı hatası: {exc.reason}") from exc


def build_url(source: PriceSource, symbol: str, days: int) -> str:
    if source.kind == "index":
        end = datetime.now(tz=TRT)
        start = end - timedelta(days=days)
        params = {
            "endeks": symbol.upper(),
            "period": "1440",
            "from": start.strftime("%Y%m%d%H%M%S"),
            "to": end.strftime("%Y%m%d%H%M%S"),
        }
        return ISYATIRIM_INDEX_URL + "?" + urllib.parse.urlencode(params)
    return YAHOO_CHART_URL.format(symbol=urllib.parse.quote(f"{symbol.upper()}{source.suffix}"))


def fetch_series(
    symbol: str,
    source: PriceSource,
    *,
    days: int = 420,
    cache_dir: Path | None = None,
    max_age_hours: float = 12.0,
    timeout: float = 30.0,
    fetcher: Callable[[str], bytes] | None = None,
) -> Series:
    """Tek enstrümanın günlük kapanışları (disk önbellekli)."""
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{source.kind}_{symbol.upper()}.json"
        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < max_age_hours:
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    bars = [(date.fromisoformat(d), float(c)) for d, c in cached.get("bars", [])]
                    return _bars_to_series(symbol, bars)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass  # bozuk önbellek: yeniden indir

    url = build_url(source, symbol, days)
    raw = (fetcher or (lambda u: _read(u, timeout)))(url)
    payload = json.loads(raw.decode("utf-8", "replace").lstrip("\ufeff"))

    series = (
        parse_isyatirim_index(payload, symbol)
        if source.kind == "index"
        else parse_yahoo_chart(payload, symbol)
    )

    if cache_file is not None:
        cache_file.write_text(
            json.dumps(
                {"symbol": symbol, "bars": [[d.isoformat(), c] for d, c in series.bars]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return series
