"""Paketin kanıt ve zaman sınırları; AI'nın veya fiyat yönünün testi değil."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bistman.context_packet import MAX_CHARS, PacketHeld, PublishedRow, build_packet, compact_json
from bistman.digest import dispatch
from bistman.notify import FakeNotifier
from bistman.pipeline import ProcessedDisclosure
from bistman.sources.base import DisclosureDetail, DisclosureStub, FundamentalRow
from bistman.triage import Route

AT = datetime(2026, 11, 17, 10, tzinfo=timezone.utc)
QUOTE = "Şirketimiz ürün teslimi için 60 milyon TL bedelli satış sözleşmesi imzalamıştır."
STUB = DisclosureStub(11, AT, "Deneme A.Ş.", "Sözleşme", tickers=("DENEM",))
ITEM = ProcessedDisclosure(STUB, Route.PIPELINE, "scored", ticker="DENEM")
ROW = FundamentalRow("DENEM", "2026-06-30", revenue_ttm=300_000_000, source="deneme:rapor")
VERSION = PublishedRow(ROW, AT - timedelta(days=60))


def detail(text, stub=STUB):
    return DisclosureDetail(stub, text, attachment_count=0)


def sale(*rows, quote=QUOTE):
    return build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi", detail=detail(quote),
                        quote=quote, statements=rows)


def test_as_of_uses_latest_known_version_of_latest_period_only() -> None:
    revision = PublishedRow(replace(ROW, revenue_ttm=600_000_000), AT)
    after = PublishedRow(replace(ROW, period_end="2026-09-30", revenue_ttm=99), AT + timedelta(seconds=1))
    other = PublishedRow(replace(ROW, ticker="DIGER", revenue_ttm=42), AT)
    older_period = PublishedRow(replace(ROW, period_end="2026-03-31", revenue_ttm=40), AT)
    assert sale(VERSION)["olcek"]["bedel_ciro_yuzde"] == 20
    p = sale(revision, after, other, older_period, VERSION)
    assert p == sale(revision)  # kayıt sırası/saat sonrasındaki bilanço geçmişi değiştirmez
    assert p["olcek"]["bedel_ciro_yuzde"] == 10
    assert p["olcek"]["tablo_yayini"] == AT.isoformat()
    assert p["olcek"]["donem_sonu"] == ROW.period_end
    assert p["olcek"]["kaynak"] == ROW.source
    assert set(p) == {"konu", "kapsam", "hisse", "kaynak", "yayin", "kanit", "kanit_yeri", "tutar", "olcek"}


@pytest.mark.parametrize("version", [
    PublishedRow(ROW, None), PublishedRow(ROW, AT.replace(tzinfo=None)),
    PublishedRow(ROW, AT + timedelta(minutes=1)), PublishedRow(replace(ROW, ticker="DIGER"), AT),
    PublishedRow(replace(ROW, period_end="2027-12-31"), AT),
])
def test_absent_point_in_time_denominator_never_silences_confirmed_event(version) -> None:
    packet = sale(version)
    assert packet["tutar"] == {"deger": "60000000", "birim": "TRY"}
    assert "olcek" not in packet and packet["sinir"] == "haber_aninda_yayimlanmis_tablo_yok"


@pytest.mark.parametrize("row", [replace(ROW, flags=("ttm_gap",)), replace(ROW, currency="USD"),
                                  replace(ROW, revenue_ttm=0), replace(ROW, statement_type="bank")])
def test_incomparable_revenue_is_not_used(row) -> None:
    p = sale(PublishedRow(row, AT))
    assert "olcek" not in p and p["sinir"] == "karsilastirilabilir_ttm_ciro_yok"


def test_only_relevant_numbers_affect_contract_packet() -> None:
    new_balance = replace(VERSION, row=replace(ROW, equity=700_000_000, net_income_ttm=-12_000_000))
    assert compact_json(sale(new_balance)) == compact_json(sale(VERSION))
    assert sale(VERSION, quote=QUOTE.replace("60 milyon", "30 milyon"))["olcek"]["bedel_ciro_yuzde"] == 10


def test_unconverted_fx_and_ambiguous_amounts_cannot_be_used_for_scale() -> None:
    fx = sale(VERSION, quote=QUOTE.replace("TL", "EUR"))
    assert fx["tutar"]["birim"] == "EUR" and fx["sinir"] == "tarihli_kur_yok" and "olcek" not in fx
    two = sale(VERSION, quote=QUOTE + " Projenin toplam bütçesi 90 milyon TL'dir.")
    assert two["sinir"] == "ana_tutar_belirsiz" and "olcek" not in two and "tutar" not in two


@pytest.mark.parametrize(("body", "quote", "reason"), [
    ("Duruş açıklanmadı.", "Duruş açıklandı.", "alinti_kayitta_yok"),
    ("Şirkete ceza verilmesi söz konusu değildir.", "Şirkete ceza verilmesi", "alinti_cumle_sinirinda_degil"),
    ("", "", "olay_kaniti_eksik"),
    ("A" * MAX_CHARS + ".", "A" * MAX_CHARS + ".", "daha_kisa_tam_kanit_sec"),
])
def test_bad_evidence_is_held_not_silently_truncated(body, quote, reason) -> None:
    with pytest.raises(PacketHeld, match=reason):
        build_packet(ITEM, origin="sentetik", topic="sirket_aciklamasi", detail=detail(body), quote=quote)


def test_identity_and_scope_are_not_invented() -> None:
    with pytest.raises(PacketHeld, match="kaynak_dogrulanmadi"):
        build_packet(ITEM, origin="dogrulanmamis", topic="uretim_durusu", detail=detail(QUOTE), quote=QUOTE)
    unknown = replace(ITEM, status="unresolved_ticker", ticker=None)
    with pytest.raises(PacketHeld, match="unresolved_ticker"):
        build_packet(unknown, origin="sentetik", topic="sirket_aciklamasi", detail=detail(QUOTE), quote=QUOTE)
    with pytest.raises(PacketHeld, match="piyasa_kapsami_dogrulanmadi"):
        build_packet(unknown, origin="sentetik", topic="endeks_parametresi", detail=detail(QUOTE), quote=QUOTE)


def test_offline_replay_against_recorded_feed_and_comparison_cases(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import baglam_provasi as demo

    feed = demo.parse_light((demo.FIXTURE / "list_light_live.json").read_bytes())
    routes, with_amount = demo.scan_feed(feed.items)
    assert not feed.rejected and len(feed.items) == 397
    assert (routes[Route.ROUTINE], routes[Route.MECHANICAL], routes[Route.PIPELINE], with_amount) == (151, 127, 119, 0)
    outcomes = {o.case.stub.index: o for o in asyncio.run(demo.replay(demo.cases({d.index: d for d in feed.items})))}
    notifier = FakeNotifier()
    report = asyncio.run(dispatch((o.processed for o in outcomes.values()), notifier, day=AT.date(), cfg=demo.ScoringConfig.load()))
    assert [k for k, _ in notifier.sent] == ["1:ORNEK", "digest:2026-11-17"]
    assert {r.disclosure.index for r in report.ranked.digest} == {2, 3, 1698956}
    assert all(s in report.digest_text for s in ("Piyasa gündemi", "endeks ağırlıklarını", "teslimat", "10 milyon EUR"))
    ready = [o.packet for o in outcomes.values() if o.packet is not None]
    assert len(ready) == 5 and all(len(compact_json(p)) <= MAX_CHARS for p in ready)
    assert outcomes[1].processed.features.ratio_revenue == 20
    assert outcomes[1].packet["olcek"]["bedel_ciro_yuzde"] == 20
    assert outcomes[2].processed.planned_action == "digest" and outcomes[2].packet["konu"] == "uretim_durusu"
    index = outcomes[1698956].packet
    assert [p for p in ready if p["kapsam"] == "piyasa_gundemi"] == [index]
    assert "hisse" not in index and index["kanit_yeri"] == "baslik"
    assert "bulunmamaktadır." in outcomes[1699030].packet["kanit"]
    assert all("olcek" not in p for p in ready if p["konu"] != "satis_sozlesmesi")
    assert outcomes[1698981].held == "ihale_sonucu_icin_detay_gerekli"
    assert outcomes[1698691].held == "piyasa_kapsami_dogrulanmadi"


@pytest.mark.parametrize("wrong", [replace(STUB, index=99), replace(STUB, tickers=("DIGER",))])
def test_another_record_cannot_supply_packet_evidence(wrong) -> None:
    with pytest.raises(PacketHeld, match="detay_kayit_uyusmazligi"):
        build_packet(ITEM, origin="kap_arsiv", topic="satis_sozlesmesi", quote=QUOTE, detail=detail(QUOTE, wrong))


@pytest.mark.parametrize("body,quote", [
    (QUOTE + " Ancak sözleşme daha sonra feshedildi.", QUOTE),
    ("Ana projenin toplam bütçesi 1 milyar TL'dir.", "Ana projenin toplam bütçesi 1 milyar TL'dir."),
    ("Başka bir şirket 10 milyon TL bedelli satış sözleşmesi imzalamıştır.",
     "Başka bir şirket 10 milyon TL bedelli satış sözleşmesi imzalamıştır."),
])
def test_whole_sentence_alone_does_not_prove_current_issuer_sale(body, quote) -> None:
    with pytest.raises(PacketHeld, match="satis_kaniti_yetersiz"):
        build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi", quote=quote, detail=detail(body))


def test_quote_price_must_agree_with_the_rest_of_the_same_disclosure() -> None:
    body = QUOTE + " Güncel sözleşme bedeli 160 milyon TL'dir."
    with pytest.raises(PacketHeld, match="bedel_baglamla_celisiyor"):
        build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi", quote=QUOTE,
                     statements=(VERSION,), detail=detail(body))


def test_unrepresentable_scale_is_omitted_not_infinite_or_a_crash() -> None:
    p = sale(PublishedRow(replace(ROW, revenue_ttm=1e-315), AT))
    assert p["sinir"] == "oran_hesaplanamadi" and "olcek" not in p


@pytest.mark.parametrize("text", [
    "Şirketimiz ile ABC Sanayi A.Ş. arasında 60 milyon TL bedelli satış sözleşmesi imzalanmıştır.",
    "Şirketimiz ile XYZ Ltd. Şti. arasında KDV hariç 60.000.000 TL bedelli satış sözleşmesi imzalanmıştır.",
    "Şirketimiz, yurt içi müşteriyle 60.000.000 TL + KDV bedelli mal satış sözleşmesi imzalamıştır.",
    "17.11.2026 tarihinde Şirketimiz ile ABC A.Ş. arasında 60 milyon TL bedelli ihracat sözleşmesi imzalanmıştır.",
    "Şirketimizce ABC San. ve Tic. A.Ş. ile 60 milyon TL bedelli satış sözleşmesi imzalanmıştır.",
    "Şirketimiz yurt dışı müşterisinden 60 milyon TL tutarında yeni sipariş almıştır.",
    "Şirketimiz ile T.C. Sağlık Bakanlığı arasında 60 milyon TL bedelli hizmet satış sözleşmesi imzalanmıştır.",
    "Şirketimiz ile Türkiye İş Bankası A.Ş. arasında 60 milyon TL bedelli yazılım satış sözleşmesi imzalanmıştır.",
    "Şirketimiz ile Karayolları Genel Müdürlüğü arasında 60 milyon TL bedelli yapım işi sözleşmesi imzalanmıştır.",
    "Şirketimiz yurt içinde yerleşik bir firmadan 60 milyon TL bedelli trafo teslimatı işi almıştır.",
    "Uhdemizde kalan ihale kapsamında 60 milyon TL bedelli satış sözleşmesi Şirketimiz ile idare arasında imzalanmıştır.",
])
def test_realistic_turkish_kap_sale_phrasings_preserve_amount_and_scale(text: str) -> None:
    p = build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi",
                     quote=text, detail=detail(text), statements=(VERSION,))
    assert p["tutar"] == {"deger": "60000000", "birim": "TRY"}
    assert p["olcek"]["bedel_ciro_yuzde"] == 20.0


def test_prior_year_comparison_does_not_block_current_sale_amount() -> None:
    quote = "Şirketimiz ile ABC A.Ş. arasında 60 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    body = quote + " Geçen yıl aynı müşteriyle 25 milyon TL tutarında satış yapılmıştı."
    p = build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi",
                     quote=quote, detail=detail(body), statements=(VERSION,))
    assert p["tutar"] == {"deger": "60000000", "birim": "TRY"}
    assert p["olcek"]["bedel_ciro_yuzde"] == 20.0


def test_negated_cancellation_keeps_valid_sale_while_regulatory_condition_holds() -> None:
    active_body = QUOTE + " Sözleşme feshedilmemiştir."
    p = build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi",
                     quote=QUOTE, detail=detail(active_body), statements=(VERSION,))
    assert p["olcek"]["bedel_ciro_yuzde"] == 20.0

    for conditional in (
        QUOTE + " Sözleşme Rekabet Kurumu iznine tabidir.",
        QUOTE + " İşbu anlaşma bağlayıcı olmayan niyet mektubu niteliğindedir.",
        "Şirketimiz ile ABC A.Ş. arasında 60 milyon TL bedelli alım sözleşmesi imzalanmıştır.",
        "Şirketimiz ile ABC A.Ş. arasında 60 milyon TL bedelli tedarik sözleşmesi imzalanmıştır.",
        "Şirketimiz tarafından 60 milyon TL bedelli danışmanlık hizmeti işi alınmıştır.",
    ):
        q = QUOTE if conditional.startswith(QUOTE) else conditional
        with pytest.raises(PacketHeld, match="satis_kaniti_yetersiz"):
            build_packet(ITEM, origin="sentetik", topic="satis_sozlesmesi",
                         quote=q, detail=detail(conditional))


def test_sentence_boundary_blocks_abbreviation_slices_and_allows_paragraph_start() -> None:
    from bistman.parsing.kap_table import body_text
    from bistman.sources.kap import parse_detail

    for full, sliced in (
        ("İddiaların aksine Prof. Dr. Ahmet Yılmaz görevinden ayrılmıştır.",
         "Ahmet Yılmaz görevinden ayrılmıştır."),
        ("Başka bir şirket olan Örnek A.Ş. hakkında idari para cezası uygulanmıştır.",
         "hakkında idari para cezası uygulanmıştır."),
        ("Matriks Finansal Teknolojiler A.Ş. faaliyetlerine devam etmektedir.",
         "Matriks Finansal Teknolojiler A.Ş."),
    ):
        with pytest.raises(PacketHeld, match="alinti_cumle_sinirinda_degil"):
            build_packet(ITEM, origin="sentetik", topic="sirket_aciklamasi",
                         detail=detail(full), quote=sliced)

    # Gerçek MTRKS detayında 'Açıklamalar' başlığından hemen sonraki ilk cümle seçilebilmeli.
    real_detail = parse_detail((Path(__file__).parent / "fixtures/kap/detail_1699030_live.json").read_bytes())
    mtrks_text = body_text(real_detail.body_html)
    first_para = next(line for line in mtrks_text.splitlines() if line.startswith("Son günlerde"))
    first_sentence = first_para[:first_para.index("verilmiştir.") + len("verilmiştir.")]
    mtrks_item = ProcessedDisclosure(real_detail.stub, Route.PIPELINE, "no_fundamentals", ticker="MTRKS")
    pkt = build_packet(mtrks_item, origin="kap_arsiv", topic="sirket_aciklamasi",
                       detail=real_detail, quote=first_sentence)
    assert pkt["kanit_yeri"] == "govde" and pkt["kanit"] == first_sentence
