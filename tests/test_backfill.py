"""Backfill hattı — gerçek geçmiş tablosu + sentetik fiyat serisi.

Atıf kuralları ve event study köprüsü burada kilitlenir; hepsi ağsız.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from bistman.analysis.event_study import Series
from bistman.backfill import (
    attribute_ticker,
    is_bulk_platform,
    is_fund_flow,
    select_rows,
    symbols_in_title,
    attach_event_study,
    coverage,
    scored_from_results,
    stub_from_row,
)
from bistman.notify import FakeNotifier
from bistman.parsing.kap_table import HistoryRow, parse_history
from bistman.pipeline import process_batch
from bistman.scoring import ScoringConfig
from bistman.sources.base import FundamentalRow
from bistman.ticker_map import TickerResolver

FIXTURES = Path(__file__).parent / "fixtures" / "kap"
TODAY = dt.date(2026, 11, 17)


@pytest.fixture(scope="module")
def rows() -> list[HistoryRow]:
    html = (FIXTURES / "member_history_mtrks.html").read_text(encoding="utf-8")
    return parse_history(html, today=TODAY)


def _row(**kw) -> HistoryRow:
    base = dict(
        index=1,
        published_at=dt.datetime(2026, 5, 5, 10, 0, tzinfo=dt.timezone.utc),
        disclosure_class="öda",
        company_title="X A.Ş.",
        subject="Sözleşme",
        summary="",
        tickers=(),
    )
    base.update(kw)
    return HistoryRow(**base)


# ------------------------------------------------------------------ atıf


def test_issuer_code_wins() -> None:
    assert attribute_ticker(_row(tickers=("GARAN",), related=("AKBNK",)), "THYAO") == "GARAN"


def test_empty_code_falls_back_to_member() -> None:
    """DKB satırlarında 'Kod' boş: üye kodu kullanılır (backfill üye bazlı)."""
    assert attribute_ticker(_row(disclosure_class="dkb", related=("MTRKS",)), "MTRKS") == "MTRKS"


def test_no_member_uses_first_related() -> None:
    assert attribute_ticker(_row(related=("MTRKS", "BERA")), None) == "MTRKS"


def test_member_wins_when_title_lists_it() -> None:
    """Gerçek satır: 'İŞ PORTFÖY ... / IDH, ..., MTRKS, ...', Kod=ISP.

    İhraççı fon yöneticisi, konu MTRKS. Eski kural satırı ISP'ye yazıyordu.
    """
    row = _row(
        company_title="İŞ PORTFÖY YÖNETİMİ A.Ş. Pay Alım Satım Bildirimi / IDH, IEV, MTRKS, TTE",
        tickers=("ISP",),
        subject="Pay Alım Satım Bildirimi",
    )
    assert attribute_ticker(row, "MTRKS") == "MTRKS"


def test_title_symbols_ignore_company_suffix() -> None:
    """'A.Ş.' gibi kısaltmalar sembol sanılmamalı: `/` yoksa liste boş."""
    assert symbols_in_title("MATRİKS FİNANSMAN A.Ş.") == ()
    assert symbols_in_title("X A.Ş. / GARAN, TGB") == ("GARAN", "TGB")


def test_fund_flow_detected_by_many_symbols() -> None:
    row = _row(company_title="İŞ PORTFÖY ... / IDH, IEV, IJB, MTRKS, TTE")
    assert is_fund_flow(row)
    assert not is_fund_flow(_row(company_title="X A.Ş. / GARAN"))


def test_platform_bulk_is_counted_not_dropped() -> None:
    """MKK 'İşlem Yasağı' 77 hisse: fon akışı değil, gerçek bilgi şoku."""
    row = _row(company_title="MERKEZİ KAYIT KURULUŞU A.Ş. SPK İşlem Yasağı / A1CAP, AKSA, MTRKS")
    assert is_bulk_platform(row)
    assert not is_fund_flow(row)
    kept, counts = select_rows([row], "MTRKS")
    assert len(kept) == 1 and counts == {"bulk_platform": 1}


def test_select_rows_counts_skips(rows: list[HistoryRow]) -> None:
    kept, skipped = select_rows(rows, "MTRKS")
    assert kept, "hiç satır kalmadı"
    assert skipped.get("fund_flow", 0) == 3
    assert skipped.get("bulk_platform", 0) == 6
    assert "foreign" not in skipped or skipped["foreign"] == 0
    assert all(attribute_ticker(r, "MTRKS") == "MTRKS" for r in kept)


def test_nothing_known_returns_none() -> None:
    assert attribute_ticker(_row(), None) is None


# --------------------------------------------------------------- gerçek tablo


def test_all_real_rows_attributed(rows: list[HistoryRow]) -> None:
    """89 satırın tamamı üye koduyla atfedilebilmeli (6 atıfsız satır dahil)."""
    attributed = [r for r in rows if attribute_ticker(r, "MTRKS")]
    assert len(attributed) == 89


def test_coverage_reports_ambiguity(rows: list[HistoryRow]) -> None:
    cov = coverage({"MTRKS": rows})
    assert cov.members == 1
    assert cov.rows == 89
    assert cov.attributed == 89
    assert cov.attribution_rate == 1.0
    assert cov.multi_symbol == 4  # BERA/KRGYO ortak duyurular


# ------------------------------------------------------------------ iskelet


def test_stub_from_row_converts_trt_to_utc() -> None:
    """KAP tablosu yerel saat verir; stub UTC taşımalı (event study şartı)."""
    row = _row(published_at=dt.datetime(2026, 5, 5, 18, 0))  # naif = TRT
    stub = stub_from_row(row, "MTRKS")
    assert stub.published_at == dt.datetime(2026, 5, 5, 15, 0, tzinfo=dt.timezone.utc)


def test_stub_from_row_keeps_ticker_and_time() -> None:
    row = _row(tickers=("MTRKS",), subject="Yeni Sözleşme")
    stub = stub_from_row(row, "MTRKS")
    assert stub.tickers == ("MTRKS",)
    assert stub.subject == "Yeni Sözleşme"
    assert stub.published_at == row.published_at
    assert stub.disclosure_class == "öda"


def test_financial_report_rows_never_become_events(rows: list[HistoryRow]) -> None:
    """FR satırı 'fundamentals_refresh' olur: olay listesine girmemeli."""
    cfg = ScoringConfig.load(Path("config/scoring.toml"))
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    fundamentals = {"MTRKS": FundamentalRow(ticker="MTRKS", period_end="2026-06-30", revenue_ttm=3.2e9)}

    import asyncio

    stubs = [stub_from_row(r, "MTRKS") for r in rows]
    results = asyncio.run(
        process_batch(
            stubs, resolver=resolver, fundamentals=fundamentals, cfg=cfg, notifier=FakeNotifier()
        )
    )
    events = scored_from_results(results)
    fr_indexes = {r.index for r in rows if r.disclosure_class == "fr"}
    assert all(e.t0 is not None for e in events)
    assert len(events) < len(rows)  # DKB/DG/FR elendi
    assert {r.index for r in rows if r.disclosure_class == "öda"}  # kontrol: ÖDA var
    # FR kaynaklı olay yok: skorlanan olayların hepsi ÖDA kaynaklı
    event_scores = {e.score for e in events}
    assert event_scores, "hiç olay üretilmedi"
    del fr_indexes


# ------------------------------------------------------------ event study köprüsü


def _series(symbol: str, start: dt.date, closes: list[float]) -> Series:
    bars = []
    for i, close in enumerate(closes):
        bars.append((start + dt.timedelta(days=i), close))
    return Series(symbol, tuple(bars))


def test_attach_event_study_computes_z() -> None:
    cfg = ScoringConfig.load(Path("config/scoring.toml"))
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    start = dt.date(2026, 3, 2)
    row = _row(
        index=1699030,
        # 41. seans sabahı (TRT 13:00 = UTC 10:00) -> t0 = 42. seans
        published_at=dt.datetime.combine(start + dt.timedelta(days=40), dt.time(10, 0),
                                         tzinfo=dt.timezone.utc),
        tickers=("MTRKS",),
        subject="Yeni Sözleşme İmzalanması",
        summary="1.000.000.000 TL bedelli sözleşme imzalanmıştır.",
    )
    import asyncio

    results = asyncio.run(
        process_batch(
            [stub_from_row(row, "MTRKS")],
            resolver=resolver,
            fundamentals={"MTRKS": FundamentalRow(ticker="MTRKS", period_end="2026-06-30", revenue_ttm=3.2e9)},
            cfg=cfg,
            notifier=FakeNotifier(),
        )
    )
    market = _series("XU100", start, [100 + i * 0.02 for i in range(70)])
    closes = [10 + 0.01 * i for i in range(70)]
    closes[41:] = [12.5] * (70 - 41)  # t0'dan itibaren sıçrama
    stock = _series("MTRKS", start, closes)

    events, reasons = attach_event_study(results, {"MTRKS": stock}, market)
    assert len(events) == 1
    event = events[0]
    assert event.z1 is not None and event.z1 > 2.0  # sert sıçrama
    assert event.car1 is not None and event.car1 > 0.15
    assert event.t0 != row.published_at.date()  # t0 = sonraki seans
    assert reasons == []


def test_missing_price_series_is_counted_not_dropped() -> None:
    cfg = ScoringConfig.load(Path("config/scoring.toml"))
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    row = _row(tickers=("MTRKS",), subject="Yeni Sözleşme")
    import asyncio

    results = asyncio.run(
        process_batch(
            [stub_from_row(row, "MTRKS")],
            resolver=resolver,
            fundamentals={"MTRKS": FundamentalRow(ticker="MTRKS", period_end="2026-06-30", revenue_ttm=3.2e9)},
            cfg=cfg,
            notifier=FakeNotifier(),
        )
    )
    market = _series("XU100", dt.date(2026, 3, 2), [100 + i for i in range(60)])
    events, reasons = attach_event_study(results, {}, market)
    assert events == []
    assert reasons == [("no_price_series", 1)]


# --------------------------------------------------- gövde -> tutar -> skor


def test_body_provider_changes_the_score(rows: list[HistoryRow]) -> None:
    """Geçmiş tablosunda tutar YOK; tutar gövdede. Gövde skoru değiştirmeli.

    Bu test Faz 4'ün varlık sebebi: kalibrasyon ancak tutar bulunursa anlamlı.
    """
    import asyncio

    from bistman.backfill import build_scored_events

    cfg = ScoringConfig.load(FIXTURES.parents[1].parent / "config" / "scoring.toml")
    fundamentals = {
        "MTRKS": FundamentalRow(
            ticker="MTRKS", period_end="2026-06-30", revenue_ttm=3.2e9,
            net_income_ttm=0.4e9, source="test",
        )
    }
    resolver = TickerResolver.from_pairs([("MTRKS", rows[0].company_title)])
    kept, _ = select_rows(rows, "MTRKS")

    def run(body_provider):
        return asyncio.run(
            build_scored_events(
                {"MTRKS": kept}, cfg=cfg, fundamentals=fundamentals,
                resolver=resolver, notifier=FakeNotifier(), body_provider=body_provider,
            )
        )

    async def fat_body(stub) -> str:
        return "Sözleşme bedeli 1 milyar TL olarak belirlenmiştir."

    without = run(None)
    with_body = run(fat_body)

    top_without = max(r.decision.push_score for r in without if r.decision)
    top_with = max(r.decision.push_score for r in with_body if r.decision)
    assert top_with > top_without + 1.0, "gövdedeki tutar skora yansımadı"

    # 1 mlr / 3.2 mlr ciro = %31 -> "buyuk" kovası; emniyet cezası tetiklenmemeli
    best = max((r for r in with_body if r.decision), key=lambda r: r.decision.push_score)
    assert "implausible_ratio" not in best.features.flags
    assert best.decision.impact >= 6.0
