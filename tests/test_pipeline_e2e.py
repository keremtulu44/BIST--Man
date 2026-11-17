"""Uçtan uca dikey dilim: bildirim -> triyaj -> hisse -> oran -> skor -> bildirim.

Ağ yok, DB yok, LLM yok. Ama hattın TAMAMI birlikte çalışıyor.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bistman.notify import FakeNotifier
from bistman.pipeline import process_batch, render_message, summarize
from bistman.scoring import ScoringConfig
from bistman.sources.base import DisclosureStub, FundamentalRow
from bistman.ticker_map import TickerResolver

UTC = timezone.utc
CFG = ScoringConfig.load()

RESOLVER = TickerResolver.from_pairs(
    [
        ("THYAO", "TÜRK HAVA YOLLARI A.O."),
        ("AKBNK", "AKBANK T.A.Ş."),
    ]
)

FUNDAMENTALS = {
    "THYAO": FundamentalRow(
        ticker="THYAO",
        period_end="2026-09-30",
        revenue_ttm=500_000_000_000.0,
        net_income_ttm=50_000_000_000.0,
        source="test",
    ),
    "AKBNK": FundamentalRow(
        ticker="AKBNK",
        period_end="2026-09-30",
        statement_type="bank",
        revenue_ttm=200_000_000_000.0,
        source="test",
    ),
}


def stub(
    index: int,
    title: str,
    subject: str,
    *,
    summary: str | None = None,
    klass: str | None = None,
    tickers: tuple[str, ...] = (),
) -> DisclosureStub:
    return DisclosureStub(
        index=index,
        published_at=datetime(2026, 11, 17, 11, 0, tzinfo=UTC),
        title=title,
        subject=subject,
        summary=summary,
        disclosure_class=klass,
        tickers=tickers,
    )


DISCLOSURES = [
    # 1) büyük sözleşme -> push
    stub(
        10,
        "TÜRK HAVA YOLLARI A.O.",
        "Yeni Sözleşme İmzalanması",
        summary="60.000.000.000 TL tutarında yeni sözleşme imzalanmıştır.",
        klass="ODA",
    ),
    # 2) rutin -> hiç skorlanmaz
    stub(11, "TÜRK HAVA YOLLARI A.O.", "Şirket Genel Bilgi Formu", klass="ODA"),
    # 3) devre kesici -> mekanik (sayaç)
    stub(
        12,
        "BORSA İSTANBUL BISTECH DEVRE KESİCİ UYGULAMASI",
        "Pay Bazında Devre Kesici Bildirimi",
        summary="THYAO.E işlem sırasında devre kesici uygulaması devreye girmiştir",
        klass="DKB",
    ),
    # 4) unvan eşleşmiyor -> unresolved
    stub(13, "BİLİNMEYEN ŞİRKET A.Ş.", "Yeni Sözleşme İmzalanması", klass="ODA"),
    # 5) finansal rapor -> veri tazeleme (push değil)
    stub(14, "AKBANK T.A.Ş.", "Finansal Rapor", klass="FR"),
    # 6) tutarsız söylenti -> tutar yok, düşük skor
    stub(15, "TÜRK HAVA YOLLARI A.O.", "Haber ve Söylentilere İlişkin Açıklama", klass="ODA"),
    # 7) yapısal ticker var (detay ucundan gelmiş gibi) -> eşleme gerekmez
    stub(
        16,
        "AK PORTFÖY BİRİNCİ FON",
        "Özel Durum Açıklaması (Genel)",
        summary="12.000.000.000 TL tutarında işlem",
        klass="ODA",
        tickers=("AKBNK",),
    ),
]


def run(disclosures=DISCLOSURES, notifier=None, fundamentals=FUNDAMENTALS):
    notifier = notifier if notifier is not None else FakeNotifier()
    results = asyncio.run(
        process_batch(
            list(disclosures),
            resolver=RESOLVER,
            fundamentals=fundamentals,
            cfg=CFG,
            notifier=notifier,
        )
    )
    return results, notifier


def test_all_disclosures_produce_a_result_no_exception() -> None:
    results, _ = run()
    assert len(results) == len(DISCLOSURES)


def test_statuses_are_correct() -> None:
    results, _ = run()
    by_index = {r.disclosure.index: r for r in results}
    assert by_index[10].status == "scored"
    assert by_index[11].status == "filtered_routine"
    assert by_index[12].status == "filtered_mechanical"
    assert by_index[13].status == "unresolved_ticker"
    assert by_index[14].status == "fundamentals_refresh"
    assert by_index[15].status == "scored"


def test_big_contract_is_pushed_with_reasoning() -> None:
    results, notifier = run()
    pushed = [r for r in results if r.is_notified]
    assert [r.disclosure.index for r in pushed] == [10]
    assert notifier.count == 1

    key, message = notifier.sent[0]
    assert key == "10:THYAO"
    assert "THYAO" in message
    assert "sözleşme" in message
    assert "impact" in message and "emniyet" in message
    # Tutar parser'ı devrede: %12 oranı gerekçede görünmeli
    assert "%12.00" in message


def test_routine_and_mechanical_never_reach_the_notifier() -> None:
    results, notifier = run()
    notified_keys = {k for k, _ in notifier.sent}
    assert "11:THYAO" not in notified_keys
    assert "12:THYAO" not in notified_keys
    assert "14:AKBNK" not in notified_keys


def test_unresolved_ticker_has_actionable_note() -> None:
    results, _ = run()
    unresolved = next(r for r in results if r.status == "unresolved_ticker")
    assert unresolved.ticker is None
    assert any("unvan→kod" in n for n in unresolved.notes)


def test_structured_ticker_bypasses_name_matching() -> None:
    """Detay ucu ticker verdiyse (fon unvanı olsa da) o kullanılır."""
    results, _ = run()
    item = next(r for r in results if r.disclosure.index == 16)
    assert item.ticker == "AKBNK"
    assert item.features is not None and item.features.ratio_revenue is not None


def test_missing_fundamentals_still_scores_but_flags() -> None:
    results, notifier = run(fundamentals={})
    item = next(r for r in results if r.disclosure.index == 10)
    assert item.status == "no_fundamentals"
    assert item.features is not None
    assert "no_denominator" in item.features.flags
    assert item.decision is not None
    # Oran hesaplanamadığı için push edilmemeli
    assert item.is_notified is False
    assert any("finansal veri yok" in n for n in item.notes)


def test_fx_missing_does_not_double_penalise() -> None:
    """'500 milyon dolar' + kur yok -> fx_fallback (no_amount DEĞİL)."""
    disclosures = [
        stub(
            20,
            "TÜRK HAVA YOLLARI A.O.",
            "Yeni Sözleşme İmzalanması",
            summary="500 milyon dolar tutarında sözleşme",
            klass="ODA",
        )
    ]
    results, _ = run(disclosures=disclosures)
    item = results[0]
    assert item.features is not None
    assert "fx_fallback" in item.features.flags
    assert "no_amount" not in item.features.flags
    assert item.decision is not None
    assert item.decision.confidence == pytest.approx(0.70)


def test_summarize_counts() -> None:
    results, _ = run()
    counts = summarize(results)
    # 10 (büyük sözleşme), 15 (tutarsız söylenti), 16 (yapısal ticker) skorlandı
    assert counts["scored"] == 3
    assert counts["filtered_routine"] == 1
    assert counts["filtered_mechanical"] == 1
    assert counts["unresolved_ticker"] == 1
    assert counts["fundamentals_refresh"] == 1
    assert counts["notified"] == 1


def test_render_message_is_plain_text() -> None:
    """MarkdownV2 kaçırma derdi olmasın diye düz metin üretiyoruz."""
    results, _ = run()
    item = next(r for r in results if r.disclosure.index == 10)
    message = render_message(item)
    assert "*" not in message and "_" not in message and "`" not in message


def test_notifier_failure_does_not_break_batch() -> None:
    notifier = FakeNotifier(fail=True)
    results, _ = run(notifier=notifier)
    assert len(results) == len(DISCLOSURES)
    assert notifier.count == 0


def test_fund_titles_do_not_pollute_unresolved_queue() -> None:
    """Fon bildirimi 'eşleşmeyen hisse' sayılmamalı; ayrı durum olmalı."""
    disclosures = [
        stub(
            30,
            "INVEO PORTFÖY YÖNETİMİ A.Ş. BİRİNCİ SERBEST FON",
            "Fon İhraç Sözleşmesi",
            klass="ODA",
        ),
        stub(31, "BİLİNMEYEN SANAYİ A.Ş.", "Yeni Sözleşme İmzalanması", klass="ODA"),
    ]
    results, _ = run(disclosures=disclosures)
    assert results[0].status == "filtered_fund"
    assert results[1].status == "unresolved_ticker"


def test_body_provider_recovers_amount_from_detail() -> None:
    """Özette tutar yoksa gövde denenir; bulunursa push eşiği geçilebilir."""
    disclosures = [
        stub(
            40,
            "TÜRK HAVA YOLLARI A.O.",
            "Yeni Sözleşme İmzalanması",
            summary="Yeni bir sözleşme imzalanmıştır.",  # tutar yok
            klass="ODA",
        )
    ]

    calls: list[int] = []

    async def body_provider(d: DisclosureStub) -> str | None:
        calls.append(d.index)
        return "<p>Sözleşme bedeli 60.000.000.000 TL'dir.</p>"

    notifier = FakeNotifier()
    results = asyncio.run(
        process_batch(
            disclosures,
            resolver=RESOLVER,
            fundamentals=FUNDAMENTALS,
            cfg=CFG,
            notifier=notifier,
            body_provider=body_provider,
        )
    )
    assert calls == [40]  # gövde yalnız aday için istendi
    assert results[0].features is not None
    assert results[0].features.ratio_revenue == pytest.approx(12.0)
    assert results[0].decision is not None and results[0].decision.action == "push"
    assert notifier.count == 1


def test_body_provider_not_called_when_summary_has_amount() -> None:
    called: list[int] = []

    async def body_provider(d: DisclosureStub) -> str | None:
        called.append(d.index)
        return None

    asyncio.run(
        process_batch(
            [DISCLOSURES[0]],  # özetinde 60 milyar TL var
            resolver=RESOLVER,
            fundamentals=FUNDAMENTALS,
            cfg=CFG,
            body_provider=body_provider,
        )
    )
    assert called == []  # gereksiz istek yok
