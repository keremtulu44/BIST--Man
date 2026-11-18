"""Konfigürasyon yükleme (stdlib `tomllib`; pydantic Faz 3'te API katmanıyla gelir)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "universe.toml"


@dataclass(frozen=True, slots=True)
class Instrument:
    kind: str  # 'fx' | 'index'
    symbol: str
    label: str
    quote_ccy: str = "TRY"
    tl_conversion_required: bool = False


@dataclass(frozen=True, slots=True)
class Config:
    index_source: str
    fallback_file: Path
    market_indices: tuple[str, ...]
    instruments: tuple[Instrument, ...]
    alert_sigma: float
    sigma_window_days: int
    poll_interval_market: int

    @property
    def usdtry_required(self) -> bool:
        """TL çevrimi gereken enstrüman var mı (Brent gibi USD fiyatlılar)."""
        return any(i.tl_conversion_required for i in self.instruments)


def load_config(path: Path | None = None) -> Config:
    raw = tomllib.loads((path or DEFAULT_CONFIG).read_text(encoding="utf-8"))

    stocks = raw["stocks"]
    commodities = raw.get("commodities", {})
    alerts = raw.get("commodity_alerts", {})
    disclosure = raw.get("disclosure", {})

    instruments = tuple(
        Instrument(
            kind=i["kind"],
            symbol=i["symbol"],
            label=i["label"],
            quote_ccy=i.get("quote_ccy", "TRY"),
            tl_conversion_required=bool(i.get("tl_conversion_required", False)),
        )
        for i in commodities.get("instruments", [])
    )

    return Config(
        index_source=stocks["source"],
        fallback_file=REPO_ROOT / stocks.get("fallback_file", "config/universe_fallback.txt"),
        market_indices=tuple(raw.get("market_reference", {}).get("indices", [])),
        instruments=instruments,
        alert_sigma=float(alerts.get("alert_sigma", 2.0)),
        sigma_window_days=int(alerts.get("sigma_window_days", 20)),
        poll_interval_market=int(disclosure.get("poll_interval_seconds_market", 15)),
    )
