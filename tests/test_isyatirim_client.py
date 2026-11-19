"""`IsYatirimClient` — ağ YOK: sahte taşıyıcı + GERÇEK sayılar.

Rakamlar kullanıcının makinesinden gelen doğrulama raporundan
(bkz. docs/dogrulama-sonuc4.json özeti, 17.11.2026).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from bistman.sources.isyatirim_client import (
    IsYatirimClient,
    IsYatirimError,
    quarters_back,
    years_back,
)

# --- gerçek yanıt iskeleti ---------------------------------------------------

THYAO_ROWS = [
    {"itemCode": "3C", "itemDescTr": "Satış Gelirleri"},
    {"itemCode": "3J", "itemDescTr": "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI"},
    {"itemCode": "2N", "itemDescTr": "Özkaynaklar"},
    {"itemCode": "1BL", "itemDescTr": "TOPLAM VARLIKLAR"},
    {"itemCode": "4B", "itemDescTr": "  Dönem Karı Vergi Yükümlülüğü"},
    {"itemCode": "2N5", "itemDescTr": "DÖNEM KARI (ZARARI)"},
]

# (yıl, ay) -> değer, milyar TL (kümülatif)
THYAO_VALUES = {
    (2024, 9): 551.928, (2024, 12): 975.711,
    (2025, 3): 176.712, (2025, 6): 408.036, (2025, 9): 690.825, (2025, 12): 1187.816,
    (2026, 3): 257.961, (2026, 6): 585.069,
}
NI_VALUES = {
    (2024, 9): 88.831, (2024, 12): 148.376,
    (2025, 3): -1.854, (2025, 6): 24.935, (2025, 9): 80.951, (2025, 12): 146.840,
    (2026, 3): 9.856, (2026, 6): 18.766,
}
EQ_VALUES = {
    (2024, 9): 618.691, (2024, 12): 890.084,
    (2025, 3): 717.436, (2025, 6): 752.120, (2025, 9): 843.951, (2025, 12): 1132.848,
    (2026, 3): 966.363, (2026, 6): 1018.453,
}
ANNUAL_REV = {(2025, 12): 1187.816, (2024, 12): 975.711, (2023, 12): 800.0, (2022, 12): 500.0}


def fake_transport(periods_seen: list | None = None):
    """URL'deki dönemleri çözüp gerçek sayılarla yanıt üretir."""

    def fetch(url: str) -> bytes:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        periods = []
        for i in range(1, 5):
            if f"year{i}" in query:
                periods.append((int(query[f"year{i}"][0]), int(query[f"period{i}"][0])))
        if periods_seen is not None:
            periods_seen.append(periods)

        table = {
            "3C": THYAO_VALUES,
            "3J": NI_VALUES,
            "2N": EQ_VALUES,
            "1BL": EQ_VALUES,
            "4B": {p: 0.5 for p in THYAO_VALUES},
            "2N5": {p: 62.0 for p in THYAO_VALUES},
        }
        rows = []
        for row in THYAO_ROWS:
            source = table[row["itemCode"]]
            if len(periods) and periods[0][1] == 12 and periods[0][0] <= 2025:
                source = ANNUAL_REV if row["itemCode"] == "3C" else {p: source.get(p) for p in periods}
            entry = {"itemCode": row["itemCode"], "itemDescTr": row["itemDescTr"]}
            for i, period in enumerate(periods, 1):
                value = source.get(period)
                entry[f"value{i}"] = None if value is None else value * 1e9
            rows.append(entry)
        return json.dumps({"value": rows}).encode("utf-8")

    return fetch


def run(coro):
    return asyncio.run(coro)


# --- dönem listeleri ---------------------------------------------------------


def test_quarters_back_publication_lag() -> None:
    assert quarters_back(1, date(2026, 1, 15)) == [(2025, 9)]
    assert quarters_back(1, date(2026, 4, 15)) == [(2025, 12)]
    assert quarters_back(1, date(2026, 7, 15)) == [(2026, 3)]
    assert quarters_back(1, date(2026, 11, 17)) == [(2026, 6)]
    assert quarters_back(1, date(2026, 12, 20)) == [(2026, 9)]


def test_quarters_back_walks_years() -> None:
    periods = quarters_back(8, date(2026, 11, 17))
    assert periods[:4] == [(2026, 6), (2026, 3), (2025, 12), (2025, 9)]
    assert periods[-1] == (2024, 9)


def test_years_back() -> None:
    assert years_back(4, date(2026, 11, 17)) == [(2025, 12), (2024, 12), (2023, 12), (2022, 12)]


# --- taşıyıcı ----------------------------------------------------------------


def test_chunking_never_exceeds_four_periods() -> None:
    """Sunucu sınırı 4: 8 çeyrek 2 istek, 4 yıl 1 istek olmalı."""
    seen: list = []
    client = IsYatirimClient(fetcher=fake_transport(seen))
    run(client.fetch_items("THYAO", periods=quarters_back(8, date(2026, 11, 17))))
    assert len(seen) == 2
    assert all(len(chunk) <= 4 for chunk in seen)


def test_items_merged_across_chunks() -> None:
    client = IsYatirimClient(fetcher=fake_transport())
    items = run(client.fetch_items("THYAO", periods=quarters_back(8, date(2026, 11, 17))))
    revenue = next(i for i in items if i.code == "3C")
    assert len(revenue.values) == 8
    assert revenue.values[(2026, 6)] == pytest.approx(585_069_000_000)


def test_non_json_response_raises() -> None:
    client = IsYatirimClient(fetcher=lambda url: b"<html>engel</html>")
    with pytest.raises(IsYatirimError, match="JSON değil"):
        run(client.fetch_items("THYAO", periods=[(2026, 6)]))


def test_empty_payload_raises() -> None:
    client = IsYatirimClient(fetcher=lambda url: b'{"value": []}')
    with pytest.raises(IsYatirimError, match="boş yanıt"):
        run(client.fetch_items("THYAO", periods=[(2026, 6)]))


# --- birleşik sonuç ----------------------------------------------------------


@pytest.fixture(scope="module")
def row():
    client = IsYatirimClient(fetcher=fake_transport())
    return run(client.fundamentals("THYAO", today=date(2026, 11, 17)))


def test_revenue_ttm_is_cumulative_corrected(row) -> None:
    """1.99x hata önlendi: 2.72 trilyon değil 1.36 trilyon."""
    assert row.revenue_ttm == pytest.approx(1_364_849_000_000)
    assert row.revenue_ttm != pytest.approx(2_721_671_000_000)


def test_net_income_uses_income_table_row(row) -> None:
    """'DÖNEM KARI (ZARARI)' bilanço kalemi değil; gelir tablosu (3J) kullanılır."""
    assert row.net_income_ttm == pytest.approx(140_671_000_000)


def test_equity_is_stock_not_ttm(row) -> None:
    assert row.equity == pytest.approx(1_018_453_000_000)


def test_naive_mismatch_is_flagged(row) -> None:
    assert "naive_sum_mismatch" in row.flags


def test_missing_market_cap_is_flagged(row) -> None:
    assert "no_market_cap" in row.flags


def test_period_end_from_latest_observation(row) -> None:
    assert row.period_end == "2026-06-30"


def test_bank_template_uses_ufrs_group() -> None:
    seen: list = []
    client = IsYatirimClient(fetcher=fake_transport(seen))
    run(client.fetch_items("AKBNK", statement_type="bank", periods=[(2026, 6)]))
    assert len(seen) == 1


def test_source_is_labelled(row) -> None:
    assert row.source == "isyatirim/malitablo"


def test_satis_hasilati_label_matches_at_full_score() -> None:
    from bistman.sources.isyatirim_labels import select_row

    pick = select_row(["Diğer Gelirler", "Satış Hasılatı"], "revenue")
    assert pick is not None and pick.label == "Satış Hasılatı" and pick.score >= 8
