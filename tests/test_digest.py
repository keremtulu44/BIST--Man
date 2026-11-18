"""Sıralama + günlük özet — saf fonksiyonlar, ağ yok."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from bistman.digest import dispatch, rank, render_digest
from bistman.features import build_features
from bistman.notify import FakeNotifier
from bistman.pipeline import ProcessedDisclosure
from bistman.scoring import ScoringConfig, score
from bistman.sources.base import DisclosureStub
from bistman.triage import Route

UTC = timezone.utc
CFG = ScoringConfig.load()
DAY = date(2026, 11, 17)


def item(
    index: int,
    ticker: str,
    amount: int,
    *,
    revenue: int = 3_200_000_000,
    subject: str = "Yeni Sözleşme İmzalanması",
    hour: int = 10,
) -> ProcessedDisclosure:
    stub = DisclosureStub(
        index=index,
        published_at=datetime(2026, 11, 17, hour, 0, tzinfo=UTC),
        title=f"{ticker} A.Ş.",
        subject=subject,
        summary="",
        disclosure_class="öda",
        tickers=(ticker,),
    )
    features = build_features(
        ticker=ticker, amount_try=Decimal(amount), revenue_ttm=Decimal(revenue)
    )
    return ProcessedDisclosure(
        disclosure=stub,
        route=Route.PIPELINE,
        status="scored",
        ticker=ticker,
        features=features,
        decision=score(features, subject, CFG),
    )


# ------------------------------------------------------------------ sıralama


def test_rank_sorts_by_score_desc() -> None:
    small = item(1, "THYAO", 120_000_000)  # %4 -> kucuk
    big = item(2, "MTRKS", 1_000_000_000)  # %31 -> buyuk
    ranked = rank([small, big])
    assert [i.ticker for i in ranked.pushes] == ["MTRKS", "THYAO"]


def test_rank_is_deterministic_on_ties() -> None:
    a = item(5, "ASELS", 1_000_000_000)
    b = item(4, "ASELS", 1_000_000_000, hour=11)
    first = rank([a, b])
    second = rank([b, a])
    assert [i.disclosure.index for i in first.pushes] == [
        i.disclosure.index for i in second.pushes
    ]
    # eşit skorda ÖNCE yayın zamanı (10:00 < 11:00), sonra index
    assert [i.disclosure.index for i in first.pushes] == [5, 4]


def test_digest_items_sorted_and_silent_excluded() -> None:
    mid = item(1, "THYAO", 60_000_000)  # %1.9 -> digest
    noise = item(2, "THYAO", 5_000_000)  # %0.15 -> silent
    other = item(3, "GARAN", 90_000_000)  # %2.8 -> digest
    ranked = rank([mid, noise, other])
    assert {i.ticker for i in ranked.digest} == {"THYAO", "GARAN"}
    assert all(i.decision.action == "digest" for i in ranked.digest)
    assert ranked.counts["silent_or_filtered"] == 1
    assert ranked.pushes == ()


def test_duplicate_disclosure_counted_not_dropped_silently() -> None:
    once = item(77, "MTRKS", 1_000_000_000)
    again = item(77, "MTRKS", 1_000_000_000)
    ranked = rank([once, again])
    assert ranked.counts["duplicate"] == 1
    assert len(ranked.pushes) + len(ranked.digest) == 1


# -------------------------------------------------------------------- bütçe


def test_budget_demotes_extra_pushes_to_digest() -> None:
    pushes = [item(i, "MTRKS", 1_000_000_000, hour=9 + i) for i in range(1, 6)]
    ranked = rank(pushes, budget_per_ticker=3)
    assert len(ranked.pushes) == 3
    assert len(ranked.demoted) == 2
    # düşenler KAYBOLMAZ: özete iner ve etiketlenir
    assert all(i in ranked.digest for i in ranked.demoted)
    assert all(any(n.startswith("bütçe:") for n in i.notes) for i in ranked.demoted)
    assert ranked.counts["demoted"] == 2


def test_budget_is_per_ticker() -> None:
    items = [item(i, "MTRKS", 1_000_000_000, hour=9 + i) for i in range(1, 5)]
    items += [item(i, "GARAN", 1_000_000_000, hour=9 + i) for i in range(10, 13)]
    ranked = rank(items, budget_per_ticker=3)
    tickers = [i.ticker for i in ranked.pushes]
    assert tickers.count("MTRKS") == 3
    assert tickers.count("GARAN") == 3
    assert len(ranked.demoted) == 1


def test_budget_from_config_is_used() -> None:
    pushes = [item(i, "MTRKS", 1_000_000_000, hour=9 + i) for i in range(1, 6)]
    notifier = FakeNotifier()
    asyncio.run(dispatch(pushes, notifier, day=DAY, cfg=CFG))
    keys = [key for key, _ in notifier.sent]
    assert len([k for k in keys if k.startswith("digest:")]) == 1
    assert len([k for k in keys if not k.startswith("digest:")]) == CFG.max_push_per_ticker_per_day


# --------------------------------------------------------------------- özet


def test_render_digest_is_plain_text_and_ordered() -> None:
    # temettü %3.1 -> 1.0+4.0=5.0 ; yatırım kararı %1.9 -> 2.0+2.0=4.0
    higher = item(1, "MTRKS", 100_000_000, subject="Temettü Dağıtımı")
    lower = item(2, "THYAO", 60_000_000, subject="Yatırım Kararı")
    ranked = rank([lower, higher])
    text = render_digest(ranked, day=DAY)
    assert text is not None
    assert "GÜNLÜK ÖZET · 17.11.2026" in text
    assert text.index("MTRKS") < text.index("THYAO"), "özet skora göre sıralı değil"
    assert "*" not in text and "_" not in text  # MarkdownV2 kaçırma derdi yok


def test_empty_digest_returns_none() -> None:
    ranked = rank([item(1, "MTRKS", 1_000_000_000)])  # yalnız push
    assert render_digest(ranked, day=DAY) is None


def test_digest_respects_max_items_and_counts_rest() -> None:
    items = [item(i, f"T{i:03d}", 200_000_000, revenue=10_000_000_000) for i in range(1, 16)]
    ranked = rank(items, budget_per_ticker=0)  # %2 -> kucuk -> hepsi özete
    text = render_digest(ranked, day=DAY, max_items=10)
    assert text is not None
    assert "olay daha" in text
    assert text.count("\n1. ") == 1  # numaralandırma bir kez başlar


def test_uncertain_push_keeps_label() -> None:
    # %469 -> implausible_ratio (x0.25) ama impact 10 -> push_uncertain
    weird = item(1, "MTRKS", 15_000_000_000)
    assert weird.decision.action == "push_uncertain"
    ranked = rank([weird])
    assert ranked.pushes[0].decision.action == "push_uncertain"


# ----------------------------------------------------------------- gönderim


def test_dispatch_sends_pushes_then_digest() -> None:
    results = [
        item(1, "MTRKS", 1_000_000_000),
        item(2, "THYAO", 60_000_000),
    ]
    notifier = FakeNotifier()
    report = asyncio.run(dispatch(results, notifier, day=DAY, cfg=CFG))
    keys = [key for key, _ in notifier.sent]
    assert keys[0] == "1:MTRKS"  # push önce
    assert keys[-1] == "digest:2026-11-17"  # özet en sonda
    assert report.pushed_ok == 1 and report.digest_sent and report.ok


def test_dispatch_reports_notifier_failure_without_raising() -> None:
    results = [item(1, "MTRKS", 1_000_000_000), item(2, "THYAO", 60_000_000)]
    report = asyncio.run(dispatch(results, FakeNotifier(fail=True), day=DAY, cfg=CFG))
    assert report.pushed_failed == 1
    assert report.digest_failed
    assert not report.ok


def test_dispatch_with_nothing_to_send_is_quiet() -> None:
    notifier = FakeNotifier()
    report = asyncio.run(dispatch([], notifier, day=DAY, cfg=CFG))
    assert notifier.count == 0
    assert report.digest_text is None
    assert report.ok


def test_dispatch_keys_are_unique() -> None:
    results = [item(i, "MTRKS", 1_000_000_000, hour=9 + i % 12) for i in range(1, 6)]
    notifier = FakeNotifier()
    asyncio.run(dispatch(results, notifier, day=DAY, cfg=CFG))
    keys = [key for key, _ in notifier.sent]
    assert len(keys) == len(set(keys))


def test_uncertain_ranks_by_impact_not_score() -> None:
    """Belirsiz ama büyük olay, düşük skoruna rağmen temettünün ÜSTÜNDE.

    Skorla sıralansaydı (%469 -> 2.50) 7.50'lik temettünün altına düşerdi;
    oysa o etiket tam da "olay büyük, emniyet düşük" demek.
    """
    from bistman.digest import sort_metric

    weird = item(1, "MTRKS", 15_000_000_000)          # impact 10, skor 2.50
    dividend = item(2, "MTRKS", 500_000_000, subject="Temettü Dağıtımı")  # 7.50
    assert weird.decision.push_score < dividend.decision.push_score
    assert sort_metric(weird) > sort_metric(dividend)
    ranked = rank([dividend, weird])
    assert ranked.pushes[0] is weird
