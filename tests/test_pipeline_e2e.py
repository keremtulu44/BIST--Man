"""Uçtan uca dikey dilim: bildirim -> triyaj -> hisse -> oran -> skor -> bildirim.

Ağ yok, DB yok, LLM yok. Ama hattın TAMAMI birlikte çalışıyor.
"""

import asyncio
from dataclasses import replace
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


@pytest.mark.parametrize(
    ("title", "tickers"),
    [
        ("TÜRK HAVA YOLLARI A.O.", ("THYAO", "AKBNK")),
        ("TÜRK HAVA YOLLARI A.O. / THYAO, AKBNK", ()),
    ],
)
def test_multi_symbol_disclosure_is_not_scored_for_an_arbitrary_company(title, tickers) -> None:
    item = stub(50, title, "Yeni Sözleşme", summary="60 milyar TL", tickers=tickers)
    [result], notifier = run([item])
    assert result.status == "unresolved_ticker"
    assert result.ticker is None and result.decision is None and notifier.count == 0


def test_snapshot_quality_reaches_the_decision() -> None:
    flagged = replace(FUNDAMENTALS["THYAO"], flags=("ttm_gap", "stale_fundamentals"))
    [result], notifier = run([DISCLOSURES[0]], fundamentals={"THYAO": flagged})
    assert result.features is not None and result.decision is not None
    assert result.features.flags == flagged.flags
    assert result.decision.confidence == pytest.approx(0.60 * 0.85)
    assert result.decision.push_score == pytest.approx(4.59)
    assert result.decision.action == "digest" and notifier.count == 0
    assert any("ttm_gap" in reason for reason in result.decision.reasons)


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


def test_body_provider_not_called_for_priced_noncontract_summary() -> None:
    called: list[int] = []

    async def body_provider(d: DisclosureStub) -> str | None:
        called.append(d.index)
        return None

    asyncio.run(
        process_batch(
            [DISCLOSURES[6]],  # sözleşme değil, özette işlem bedeli var
            resolver=RESOLVER,
            fundamentals=FUNDAMENTALS,
            cfg=CFG,
            body_provider=body_provider,
        )
    )
    assert called == []  # gereksiz istek yok


# ------------------------------------------- canlı akışta unvan yok: başlık sembolü


def _stub(index: int, title: str, subject: str, summary: str | None = None) -> DisclosureStub:
    return DisclosureStub(
        index=index,
        published_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        title=title,
        subject=subject,
        summary=summary,
        disclosure_class="dkb",
        tickers=(),
    )


def test_platform_title_with_single_symbol_resolves() -> None:
    """Canlı akışta ticker alanı yok; tek sembollü platform başlığı çözülmeli.

    Not: gerçek fixture'da bu yola düşen satırlar çoğunlukla ÇOK sembollü
    (fon akışı, piyasa geneli). Tek sembol, KAP duyurusu tek hisseyi
    ilgilendirdiğinde devreye girer; o zaman unvan eşleşmesi boş döner.
    """
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    results = asyncio.run(
        process_batch(
            [
                _stub(
                    9,
                    "KAMUYU AYDINLATMA PLATFORMU Kamuyu Aydınlatma Platformu "
                    "Duyurusu / MTRKS",
                    "Kamuyu Aydınlatma Platformu Duyurusu",
                )
            ],
            resolver=resolver,
            fundamentals={},
            cfg=CFG,
        )
    )
    assert results[0].ticker == "MTRKS"
    assert results[0].status == "no_fundamentals"  # finansal veri yok ama atıf doğru


def test_multi_symbol_title_stays_unresolved() -> None:
    """Piyasa geneli duyuru (13 satır): tek hisseye yazmak tahmin olur."""
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    results = asyncio.run(
        process_batch(
            [
                _stub(
                    10,
                    "KAMUYU AYDINLATMA PLATFORMU Kamuyu Aydınlatma Platformu "
                    "Duyurusu / AHSGY, BLA, MTRKS",
                    "Kamuyu Aydınlatma Platformu Duyurusu",
                )
            ],
            resolver=resolver,
            fundamentals={},
            cfg=CFG,
        )
    )
    assert results[0].ticker is None
    assert results[0].status == "unresolved_ticker"


def test_uncertain_push_is_labeled_in_message() -> None:
    """`push_uncertain` mesajda etiketlenmeli: skoru düşük, olayı büyük."""
    # 15 mlr sözleşme / 3.2 mlr ciro = %469 -> implausible_ratio -> push_uncertain
    stub = DisclosureStub(
        index=12,
        published_at=datetime(2026, 11, 17, 9, 30, tzinfo=UTC),
        title="MTRKS A.Ş.",
        subject="Yeni Sözleşme İmzalanması",
        summary="Sözleşme bedeli 15 milyar TL'dir.",
        disclosure_class="öda",
        tickers=("MTRKS",),
    )
    results = asyncio.run(
        process_batch(
            [stub],
            resolver=RESOLVER,
            fundamentals={
                "MTRKS": FundamentalRow(
                    ticker="MTRKS", period_end="2026-09-30",
                    revenue_ttm=3_200_000_000.0, source="test",
                )
            },
            cfg=CFG,
        )
    )
    item = results[0]
    assert item.decision.action == "push_uncertain"
    message = render_message(item)
    assert "belirsiz" in message
    assert "implausible_ratio" in message


@pytest.mark.parametrize(("notifier", "attempted", "ok"), [(None, 0, 0), (FakeNotifier(fail=True), 1, 0), (FakeNotifier(), 1, 1)])
def test_push_candidate_is_not_a_delivery_receipt(notifier, attempted, ok) -> None:
    rows = asyncio.run(process_batch([DISCLOSURES[0]], resolver=RESOLVER, cfg=CFG,
                                     fundamentals=FUNDAMENTALS, notifier=notifier))
    counts = summarize(rows)
    assert counts["notified"] == ok and rows[0].is_notified == bool(ok)
    assert counts["push_candidates"] == 1 and counts["push_attempted"] == attempted
    assert counts["push_failed"] == attempted - ok


def test_body_failure_is_local_and_auditable() -> None:
    async def missing(d):
        if d.index == DISCLOSURES[5].index:
            raise RuntimeError("eksik detay")
        return d.summary

    rows = asyncio.run(process_batch([DISCLOSURES[5], DISCLOSURES[0]], resolver=RESOLVER, cfg=CFG,
                                     fundamentals=FUNDAMENTALS, body_provider=missing))
    assert rows[0].features.amount_try is None and any("gövde" in n for n in rows[0].notes)
    assert rows[1].decision.action == "push"


@pytest.mark.parametrize(("summary", "body", "expected", "flag", "fetch"), [
    # Başka bir TL tutarı sırf kura gerek yok diye satış bedeli olamaz.
    ("10 milyon EUR bedelli sözleşme", "Sermayemiz 60 milyar TL'dir.", None, "fx_fallback", False),
    ("Eski bedel 100 milyon TL, yeni teklif 500 milyon TL.", "", None, "ambiguous_amount", False),
    # Tek TL tutarı dahi tüm projeye ait olabilir: detaydaki açık şirket payını dene.
    ("Ana projenin toplam bütçesi 80 milyar TL'dir.", "Şirketimize düşen pay 6 milyar TL'dir.", Decimal("6e9"), None, True),
    ("Ana projenin toplam bütçesi 80 milyar TL'dir.", "", None, "unattributed_amount", True),
    ("Ana projenin toplam bütçesi 80 milyar TL'dir.", "Geçen yılki ciromuz 7 milyar TL idi.", None, "unattributed_amount", True),
    # Ayrı alanlar arasından sayı ve birimi birleştirme.
    ("İşlem sırası 60", "milyar TL olarak raporlama yapılır.", None, "no_amount", True),
])
def test_amount_does_not_change_roles_across_fields(summary, body, expected, flag, fetch) -> None:
    calls = []

    async def provider(d):
        calls.append(d.index)
        return body

    [r] = asyncio.run(process_batch([replace(DISCLOSURES[0], summary=summary)], resolver=RESOLVER,
                                    cfg=CFG, fundamentals=FUNDAMENTALS, body_provider=provider))
    assert r.features.amount_try == expected and bool(calls) == fetch
    if flag:
        assert flag in r.features.flags and r.features.primary_ratio is None


@pytest.mark.parametrize("snapshot", [replace(FUNDAMENTALS["THYAO"], ticker="AKBNK"),
                                      replace(FUNDAMENTALS["THYAO"], currency="USD"),
                                      replace(FUNDAMENTALS["THYAO"], period_end="2026-12-31"),
                                      replace(FUNDAMENTALS["THYAO"], revenue_ttm=float("nan"))])
def test_incomparable_snapshot_cannot_create_a_push(snapshot) -> None:
    [r], notifier = run([DISCLOSURES[0]], fundamentals={"THYAO": snapshot})
    assert r.features.ratio_revenue is None and not r.is_notified and not notifier.count


@pytest.mark.parametrize(("body", "flag", "action"), [
    ("Ancak bu sözleşme bugün feshedildi.", "qualified_contract", "digest"),
    ("Sözleşme bedeli 20 milyar TL'dir.", "ambiguous_amount", "silent"),
    (None, "detail_unavailable", "silent"),
])
def test_priced_contract_checks_available_detail_before_pushing(body, flag, action) -> None:
    async def provider(_):
        return body

    [r] = asyncio.run(process_batch([DISCLOSURES[0]], resolver=RESOLVER, cfg=CFG,
                                    fundamentals=FUNDAMENTALS, body_provider=provider))
    assert flag in r.features.flags and r.features.primary_ratio is None
    assert r.planned_action == action


@pytest.mark.parametrize("body", ["Geçen yılki ciromuz 100 milyar TL idi.",
                                  "Başka bir şirket 60 milyar TL bedelli satış sözleşmesi imzalamıştır."])
def test_signed_event_never_borrows_an_unattributed_body_price(body) -> None:
    d = replace(DISCLOSURES[0], summary="Şirketimiz satış sözleşmesi imzalamıştır.")

    async def provider(_):
        return body

    [r] = asyncio.run(process_batch([d], resolver=RESOLVER, cfg=CFG, fundamentals=FUNDAMENTALS,
                                    body_provider=provider))
    assert r.features.amount_try is None and r.planned_action == "digest"
    assert "milyar" not in r.brief


def test_unit_price_or_unknown_fx_is_not_an_event_total() -> None:
    from bistman.features import amount_try_from_text
    assert amount_try_from_text("Pay başına 2,35 TL temettü")[0] is None
    for fx in ({"UNKNOWN": Decimal(40)}, {"EUR": Decimal(-1)}, {"EUR": Decimal("NaN")}):
        assert amount_try_from_text("Bedel 10 milyon" if "UNKNOWN" in fx else "10 milyon EUR", fx)[0] is None


def test_unconverted_signed_sale_keeps_its_currency_when_detail_has_other_financials() -> None:
    d = replace(DISCLOSURES[0], summary="Şirketimiz 10 milyon EUR bedelli satış sözleşmesi imzalamıştır.")

    async def provider(_):
        return "Ödenmiş sermayemiz 60 milyar TL'dir."

    [r] = asyncio.run(process_batch([d], resolver=RESOLVER, cfg=CFG, fundamentals=FUNDAMENTALS,
                                    body_provider=provider))
    assert r.features.amount_try is None and "fx_fallback" in r.features.flags
    assert "10 milyon EUR" in r.brief and "60 milyar" not in r.brief


def test_typed_body_must_belong_to_the_scored_company() -> None:
    from bistman.sources.base import DisclosureDetail

    d = replace(DISCLOSURES[0], summary=None)

    async def provider(_):
        return DisclosureDetail(replace(d, tickers=("AKBNK",)), "Sözleşme bedeli 90 milyar TL.", 0)

    [r] = asyncio.run(process_batch([d], resolver=RESOLVER, cfg=CFG, fundamentals=FUNDAMENTALS,
                                    body_provider=provider))
    assert r.features.amount_try is None and "detail_mismatch" in r.features.flags


def test_qualitative_agenda_does_not_send_an_instant_score_even_with_loose_threshold() -> None:
    d = replace(DISCLOSURES[0], subject="Üretime Ara Verilmesi", summary="Geçen yıl ciromuz 60 milyar TL idi.")
    notifier = FakeNotifier()
    [r] = asyncio.run(process_batch([d], resolver=RESOLVER, cfg=replace(CFG, push_threshold=0),
                                    fundamentals=FUNDAMENTALS, notifier=notifier))
    assert r.decision.should_notify and r.planned_action == "digest"
    assert r.features.primary_ratio is None and not r.is_notified and not notifier.count


def test_failed_detail_does_not_erase_a_signed_summary_or_confirm_its_price() -> None:
    d = replace(DISCLOSURES[0], summary="Şirketimiz 60 milyar TL bedelli satış sözleşmesi imzalamıştır.")

    async def missing(_):
        raise OSError("çevrimdışı")

    [r] = asyncio.run(process_batch([d], resolver=RESOLVER, cfg=CFG, fundamentals=FUNDAMENTALS, body_provider=missing))
    assert r.planned_action == "digest" and r.features.primary_ratio is None
    assert "teyit edilmedi" in r.brief and "60 milyar" not in r.brief
