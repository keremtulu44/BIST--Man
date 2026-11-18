"""Fiyat kaynakları — ağ YOK: gerçek biçimlerle ayrıştırma testleri."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from bistman.sources.prices import (
    PriceError,
    PriceSource,
    build_url,
    fetch_series,
    parse_isyatirim_index,
    parse_yahoo_chart,
)

START = 1_760_000_000  # sabit zaman damgası (2025-10-09)


def yahoo_payload(closes: list[float | None], stamps: list[int] | None = None) -> dict:
    stamps = stamps or [START + i * 86_400 for i in range(len(closes))]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ],
            "error": None,
        }
    }


# ------------------------------------------------------------------ Yahoo


def test_parse_yahoo_basic() -> None:
    series = parse_yahoo_chart(yahoo_payload([10.0, 10.5, 11.0]), "THYAO")
    assert series.symbol == "THYAO"
    assert [c for _, c in series.bars] == [10.0, 10.5, 11.0]
    assert len(series.dates) == 3


def test_parse_yahoo_skips_null_closes() -> None:
    """Tatil/işlem yok günü `null` gelir; uydurulmaz, atlanır."""
    series = parse_yahoo_chart(yahoo_payload([10.0, None, 11.0]), "THYAO")
    assert len(series.bars) == 2


def test_parse_yahoo_rejects_empty_result() -> None:
    with pytest.raises(PriceError, match="boş yanıt"):
        parse_yahoo_chart({"chart": {"result": [], "error": {"code": "Not Found"}}}, "ZZZZ")


def test_parse_yahoo_needs_two_bars() -> None:
    with pytest.raises(PriceError, match="yeterli kapanış"):
        parse_yahoo_chart(yahoo_payload([10.0]), "THYAO")


def test_parse_yahoo_ignores_non_positive() -> None:
    series = parse_yahoo_chart(yahoo_payload([10.0, 0.0, 11.0]), "THYAO")
    assert [c for _, c in series.bars] == [10.0, 11.0]


def test_timezone_is_turkey_fixed_offset() -> None:
    """Kapanış 15:10 UTC = 18:10 TRT; gün UTC'de kaymamalı."""
    stamp = 1_760_000_000  # 15:33 UTC
    series = parse_yahoo_chart(
        yahoo_payload([10.0, 10.0], [stamp, stamp + 86_400]), "THYAO"
    )
    expected = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).date()
    assert series.dates[0] == expected


# ------------------------------------------------------------- İş Yatırım


def test_parse_isyatirim_index() -> None:
    payload = {"data": [[START * 1000, 9000.5], [(START + 86_400) * 1000, 9050.0]]}
    series = parse_isyatirim_index(payload, "XU100")
    assert [c for _, c in series.bars] == [9000.5, 9050.0]


def test_parse_isyatirim_accepts_plain_list() -> None:
    series = parse_isyatirim_index([[START * 1000, 100.0], [(START + 86_400) * 1000, 101.0]], "XU100")
    assert len(series.bars) == 2


def test_parse_isyatirim_rejects_empty() -> None:
    with pytest.raises(PriceError, match="endeks verisi boş"):
        parse_isyatirim_index({"data": []}, "XU100")


def test_parse_isyatirim_skips_garbage_rows() -> None:
    payload = {"data": [[START * 1000, 100.0], ["x"], [None, 5], [(START + 86_400) * 1000, 101.0]]}
    series = parse_isyatirim_index(payload, "XU100")
    assert len(series.bars) == 2


# ------------------------------------------------------------------ URL'ler


def test_index_url_uses_isyatirim() -> None:
    url = build_url(PriceSource(kind="index"), "XU100", 400)
    assert "IndexHistoricalAll" in url
    assert "endeks=XU100" in url
    assert "period=1440" in url


def test_stock_url_uses_yahoo_with_bist_suffix() -> None:
    url = build_url(PriceSource(kind="stock"), "thyao", 400)
    assert "query1.finance.yahoo.com" in url
    assert "THYAO.IS" in url


# --------------------------------------------------------------- önbellek


def test_fetch_series_writes_and_reads_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return json.dumps(yahoo_payload([10.0, 11.0])).encode()

    first = fetch_series("THYAO", PriceSource("stock"), cache_dir=tmp_path, fetcher=fetcher)
    second = fetch_series("THYAO", PriceSource("stock"), cache_dir=tmp_path, fetcher=fetcher)

    assert len(calls) == 1  # ikinci çağrı önbellekten
    assert first.bars == second.bars
    assert (tmp_path / "stock_THYAO.json").exists()


def test_corrupt_cache_is_refetched(tmp_path: Path) -> None:
    (tmp_path / "stock_THYAO.json").write_text("{bozuk", encoding="utf-8")
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return json.dumps(yahoo_payload([10.0, 11.0])).encode()

    series = fetch_series("THYAO", PriceSource("stock"), cache_dir=tmp_path, fetcher=fetcher)
    assert len(calls) == 1
    assert len(series.bars) == 2
