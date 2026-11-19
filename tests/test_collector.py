"""Sessiz KAP toplayıcısı: listeyi bir kez oku, kanıtı doğrula, alarm geldiğinde sorgula."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from bistman.alarm_context import ReviewedReading, open_archive
from bistman.collector import (
    EvidenceProposal,
    ingest_feed,
    sync_once,
)
from bistman.fundamentals import PublishedRow
from bistman.sources.base import DisclosureDetail, DisclosureStub, FundamentalRow, ParseResult
from bistman.sources.kap import KapClient, parse_detail, parse_light
from bistman.sources.kap_companies import build_company_resolver, parse_companies
from bistman.ticker_map import TickerResolver

FIX = Path(__file__).parent / "fixtures" / "kap"
AT = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)
SEC = timedelta(seconds=1)


def test_real_light_feed_dedup_reviewer_and_pending_tender(tmp_path) -> None:
    feed = parse_light((FIX / "list_light_live.json").read_bytes())
    mtrks_detail = parse_detail((FIX / "detail_1699030_live.json").read_bytes())
    observed = datetime(2026, 11, 17, 12, 40, tzinfo=timezone.utc)

    resolver = TickerResolver.from_pairs([
        ("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."),
        ("FONET", "FONET BİLGİ TEKNOLOJİLERİ A.Ş."),
    ])

    class MtrksReviewer:
        async def propose(self, stub, ticker, detail, body):
            if stub.index != 1699030:
                return None
            a = body.index("Sermaye Piyasası Kurulunun 2024/35")
            b = body.index("bulunmamaktadır.", a) + len("bulunmamaktadır.")
            return EvidenceProposal(
                topic="sirket_aciklamasi",
                quote=body[a:b],
                reviewed=ReviewedReading(
                    "MTRKS, SPK’nın 2024/35 sayılı bültenindeki incelemede kendisine veya yöneticilerine yönelik bir yaptırım açıklanmadığını belirtti.",
                    "Şirket, payındaki işlemlere ilişkin eski kararları açıklığa kavuşturuyor; yeni bir ceza bildirmiyor.",
                ),
            )

    requested: list[int] = []

    async def fetcher(idx: int) -> DisclosureDetail:
        requested.append(idx)
        if idx == 1699030:
            return mtrks_detail
        # FONET ihalesi: henüz sonuçsuz süreç gövdesi
        fonet = next(s for s in feed.items if s.index == idx)
        return DisclosureDetail(fonet, "<p>İhale komisyonu değerlendirme süreci devam etmektedir.</p>", 0)

    db = tmp_path / "canli.sqlite"
    with open_archive(db, writable=True) as archive:
        first = asyncio.run(ingest_feed(
            feed, archive, resolver=resolver, detail_fetcher=fetcher,
            reviewer=MtrksReviewer(), universe=["MTRKS", "FONET", "THYAO"],
            clock=lambda: observed,
        ))
        assert first.total_rows == 397 and first.rejected_rows == 0
        assert first.counts["hazir"] == 1
        assert first.counts["ihale_sonucu_icin_detay_gerekli"] == 1
        assert set(requested) == {1699030, 1698981}
        assert first.scanned_until == observed

        # Aynı akış yeniden tarandığında hiçbir detay tekrar indirilmez.
        second = asyncio.run(ingest_feed(
            feed, archive, resolver=resolver, detail_fetcher=fetcher,
            reviewer=MtrksReviewer(), universe=["MTRKS", "FONET", "THYAO"],
            clock=lambda: observed + MIN,
        ))
        assert second.detail_requests == 0

    with open_archive(db) as reader:
        # 14:53:07'de yayımlanmış olsa da kanıt 15:40'ta (12:40 UTC) hazır oldu: lookahead yok.
        before_known = reader.alarm_baglami("MTRKS", datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc))
        assert before_known["durum"] == "yok"
        at_known = reader.alarm_baglami("MTRKS", observed)
        assert at_known["durum"] == "var"
        assert at_known["baglam"]["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/1699030"

        # FONET: sonuç kanıtlanamadı -> 'detay_bekleniyor', uydurma sonuç yok.
        fonet_ans = reader.alarm_baglami("FONET", observed)
        assert fonet_ans["durum"] == "yok" and fonet_ans["neden"] == "detay_bekleniyor"

        # THYAO: tarama tamamlandı ve uygun haber yok -> 'uygun_gelisme_yok'.
        thyao_ans = reader.alarm_baglami("THYAO", observed)
        assert thyao_ans["durum"] == "yok" and thyao_ans["neden"] == "uygun_gelisme_yok"
        assert thyao_ans["son_tarama"] == observed.isoformat()


def test_automatic_sale_and_stoppage_extraction_plus_cancellation_and_retry(tmp_path) -> None:
    sale_text = (
        "Ana projenin toplam bütçesi 2 milyar TL'dir. "
        "Şirketimiz ile ABC Sanayi A.Ş. arasında 200 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    )
    stop_text = "Boya hattında planlanmamış arıza nedeniyle üretime ara verilmiştir."
    s_sale = DisclosureStub(10, AT, "Örnek Sanayi A.Ş.", "Yeni İş İlişkisi")
    s_stop = DisclosureStub(11, AT + MIN, "Duruş Metal A.Ş.", "Özel Durum Açıklaması (Genel)")
    resolver = TickerResolver.from_pairs([
        ("ORNEK", "Örnek Sanayi A.Ş."),
        ("DURUS", "Duruş Metal A.Ş."),
    ])
    stmt = PublishedRow(
        FundamentalRow("ORNEK", "2026-09-30", revenue_ttm=1_000_000_000.0, source="kap:fr-2026q3"),
        AT - timedelta(days=10),
    )

    fail_stop = True

    async def fetcher(idx: int) -> DisclosureDetail:
        if idx == 10:
            return DisclosureDetail(
                DisclosureStub(10, AT, s_sale.title, s_sale.subject, disclosure_class="ODA", tickers=("ORNEK",)),
                f"<div><p>{sale_text}</p></div>", 0,
            )
        if fail_stop:
            raise OSError("geçici ağ hatası")
        return DisclosureDetail(
            DisclosureStub(11, AT + MIN, s_stop.title, s_stop.subject, disclosure_class="ODA", tickers=("DURUS",)),
            f"<div><p>{stop_text}</p></div>", 0,
        )

    feed = ParseResult(items=[s_sale, s_stop])
    with open_archive(tmp_path / "oto.sqlite", writable=True) as archive:
        # 1. tur: #10 hazır, #11 detay hatası -> tarama saati #11'in önüne çekilir.
        r1 = asyncio.run(ingest_feed(
            feed, archive, resolver=resolver, detail_fetcher=fetcher,
            statements={"ORNEK": (stmt,)}, clock=lambda: AT + 2 * MIN,
        ))
        assert r1.counts["hazir"] == 1 and r1.counts["detay_bekleniyor"] == 1
        assert r1.scanned_until == AT + MIN - timedelta(microseconds=1)

        sale_ans = archive.alarm_baglami("ORNEK", AT + 2 * MIN)
        assert sale_ans["durum"] == "var"
        assert "%20" in sale_ans["baglam"]["onemi"]
        assert sale_ans["baglam"]["mali_tablo"]["donem_sonu"] == "2026-09-30"

        pending_ans = archive.alarm_baglami("DURUS", AT + 2 * MIN)
        assert pending_ans["durum"] == "yok" and pending_ans["neden"] == "detay_bekleniyor"

        # 2. tur: ağ düzeldi -> yalnız eksik #11 indirilir, #10 tekrar indirilmez.
        fail_stop = False
        r2 = asyncio.run(ingest_feed(
            feed, archive, resolver=resolver, detail_fetcher=fetcher,
            statements={"ORNEK": (stmt,)}, clock=lambda: AT + 3 * MIN,
        ))
        assert r2.detail_requests == 1 and r2.counts["hazir"] == 1
        assert r2.scanned_until == AT + 3 * MIN
        assert archive.alarm_baglami("DURUS", AT + 2 * MIN)["durum"] == "yok"
        assert archive.alarm_baglami("DURUS", AT + 3 * MIN)["durum"] == "var"


def test_probe_unresolved_recovers_tickerless_mtrks_and_empty_feed_is_suspicious(tmp_path) -> None:
    mtrks_detail = parse_detail((FIX / "detail_1699030_live.json").read_bytes())
    light_row = DisclosureStub(
        1699030, mtrks_detail.stub.published_at,
        "FARKLI YAZILMIŞ UNVAN A.Ş.", "Haber ve Söylentilere İlişkin Açıklama",
    )

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/list/light"):
            return httpx.Response(200, json=[{
                "disclosureIndex": 1699030,
                "publishDate": "17.11.2026 14:53:07",
                "title": light_row.title,
                "subject": light_row.subject,
                "summary": light_row.subject,
            }])
        if request.url.path.endswith("/1699030"):
            return httpx.Response(200, content=(FIX / "detail_1699030_live.json").read_bytes())
        return httpx.Response(404)

    html = (
        '<html><script>self.__next_f.push([1,"'
        '{\\"mkkMemberOid\\":\\"oid1\\",\\"kapMemberTitle\\":\\"BAŞKA A.Ş.\\",'
        '\\"relatedMemberTitle\\":\\"\\",\\"stockCode\\":\\"BASKA\\"}'
        '"])</script></html>'
    )
    resolver = build_company_resolver(parse_companies(html))

    async def run_sync():
        transport = httpx.MockTransport(transport_handler)
        async with KapClient(transport=transport) as kap:
            with open_archive(tmp_path / "probe.sqlite", writable=True) as archive:
                empty = await ingest_feed(ParseResult([]), archive, resolver=resolver, clock=lambda: AT)
                assert empty.suspicious_empty is True
                assert archive.last_scan() is None

                rep = await sync_once(
                    kap, archive, resolver=resolver,
                    probe_unresolved=True, max_unresolved_probes=5,
                    clock=lambda: datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc),
                )
                assert rep.detail_requests == 1
                # İnceleyici verilmeden bile MTRKS #1699030'un birebir netleştirme cümlesi doğrulanır.
                assert rep.counts["hazir"] == 1
                ans = archive.alarm_baglami("MTRKS", datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc))
                assert ans["durum"] == "var"
                assert "yaptırım kararı bulunmamaktadır." in ans["baglam"]["olay"]

    asyncio.run(run_sync())


def test_detail_multi_ticker_retracts_earlier_single_ticker_record(tmp_path) -> None:
    sale = "Şirketimiz ile ABC A.Ş. arasında 50 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    initial = DisclosureStub(55, AT, "Tek Şirket A.Ş.", "Yeni İş İlişkisi", summary=sale)
    updated = DisclosureStub(55, AT, "Tek Şirket A.Ş.", "Yeni İş İlişkisi", summary=sale + " (Güncelleme)")
    resolver = TickerResolver.from_pairs([("TEKAS", "Tek Şirket A.Ş.")])

    async def multi_detail(_: int) -> DisclosureDetail:
        return DisclosureDetail(
            DisclosureStub(55, AT, initial.title, initial.subject, disclosure_class="ODA", tickers=("TEKAS", "ORTAK")),
            f"<p>{sale}</p>", 0,
        )

    with open_archive(tmp_path / "retract.sqlite", writable=True) as archive:
        r1 = asyncio.run(ingest_feed(
            ParseResult([initial]), archive, resolver=resolver, detail_fetcher=None, clock=lambda: AT,
        ))
        assert r1.counts["hazir"] == 1
        assert archive.alarm_baglami("TEKAS", AT)["durum"] == "var"

        r2 = asyncio.run(ingest_feed(
            ParseResult([updated]), archive, resolver=resolver,
            detail_fetcher=multi_detail, clock=lambda: AT + MIN,
        ))
        assert r2.counts["geri_cekildi"] == 1
        assert archive.alarm_baglami("TEKAS", AT + MIN - SEC)["durum"] == "var"
        assert archive.alarm_baglami("TEKAS", AT + MIN)["durum"] == "yok"


def test_bist_corporate_event_categories_and_negative_guards() -> None:
    from bistman.collector import extract_verified_evidence, is_low_signal_corporate, is_non_equity_disclosure

    positive = [
        ("İhale Süreci / Sonucu",
         "TEDAŞ tarafından düzenlenen ihalede en avantajlı teklif 350 milyon TL ile Şirketimiz tarafından verilmiş olup ihale uhdemizde kalmıştır.",
         "sirket_aciklamasi"),
        ("İhale Süreci / Sonucu",
         "Uhdemizde kalan ihale kapsamında 350 milyon TL bedelli satış sözleşmesi Şirketimiz ile idare arasında imzalanmıştır.",
         "satis_sozlesmesi"),
        ("Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim",
         "Yönetim Kurulumuz çıkarılmış sermayenin %200 oranında bedelsiz artırılarak 900.000.000 TL'ye çıkarılmasına karar vermiştir.",
         "sirket_aciklamasi"),
        ("Kar Payı Dağıtım İşlemlerine İlişkin Bildirim",
         "Pay başına brüt 4,50 TL nakit kar payı dağıtılmasına karar verilmiştir.",
         "sirket_aciklamasi"),
        ("Payların Geri Alınmasına İlişkin Bildirim",
         "Şirketimiz paylarının geri alınmasına ve 500.000.000 TL fon ayrılmasına karar verilmiştir.",
         "sirket_aciklamasi"),
        ("Volatilite Bazlı Tedbir Sistemi (VBTS)",
         "Sermaye Piyasası Kurulu kararı uyarınca ORNEK.E payları 1 ay süreyle açığa satış ve kredili işlemlere konu edilemeyecektir.",
         "sirket_aciklamasi"),
        ("Maddi Duran Varlık Satımı",
         "Şirketimiz aktifinde kayıtlı taşınmazın 300 milyon TL bedelle satılmasına karar verilmiştir.",
         "sirket_aciklamasi"),
        ("Yatırım Teşvik Belgesi",
         "Yeni üretim hattı yatırımımız için 420 milyon TL tutarında yatırım teşvik belgesi alınmıştır.",
         "sirket_aciklamasi"),
        ("Kredi Derecelendirmesi",
         "JCR Avrasya tarafından Şirketimizin uzun vadeli ulusal notu AA+ (tr) olarak teyit edilmiştir.",
         "sirket_aciklamasi"),
        ("Pay Alım Satım Bildirimi",
         "Bu işlemle birlikte Örnek A.Ş. sermayesindeki paylarım/oy haklarım 17.11.2026 tarihi itibarıyla %5,20 sınırına ulaşmıştır.",
         "sirket_aciklamasi"),
    ]
    for subj, body, exp_topic in positive:
        stub = DisclosureStub(1, AT, "Örnek A.Ş.", subj, disclosure_class="ODA", tickers=("ORNEK",))
        prop = extract_verified_evidence(stub, body)
        assert prop is not None and prop.topic == exp_topic and prop.quote == body, subj
        if subj == "İhale Süreci / Sonucu" and exp_topic == "sirket_aciklamasi":
            assert prop.reviewed is not None and "sözleşme imzası" in prop.reviewed.significance

    # İptal edilen/henüz sonuçlanmayan ihaleler ve salt şablon cümleleri seçilmez.
    for subj, body in (
        ("İhale Süreci / Sonucu", "İhale komisyonu değerlendirme süreci devam etmektedir."),
        ("İhale Süreci / Sonucu", "Söz konusu ihale idare tarafından iptal edilmiştir."),
        ("Özel Durum Açıklaması (Genel)", "Kamuoyuna ve yatırımcılarımıza saygıyla duyurulur."),
    ):
        stub = DisclosureStub(2, AT, "Örnek A.Ş.", subj, disclosure_class="ODA", tickers=("ORNEK",))
        assert extract_verified_evidence(stub, body) is None, body

    assert is_low_signal_corporate("Herhangi Bir Otoriteye Mali Tablo Verilmesi") is True
    assert is_low_signal_corporate("Bağımsız Denetim Kuruluşunun Belirlenmesi") is True
    assert is_non_equity_disclosure(
        "KAMUYU AYDINLATMA PLATFORMU", "Kamuyu Aydınlatma Platformu Duyurusu",
        "Yatırım Fonlarına İlişkin Rehber'in 9.8. maddesi gereğince yapılan açıklama",
    ) is True


def test_opaque_issuer_probe_and_strict_watermark_honesty(tmp_path) -> None:
    insider_body = (
        "Bu işlemle birlikte Matriks A.Ş. sermayesindeki paylarım 17.11.2026 tarihi itibarıyla %10,15 sınırına ulaşmıştır."
    )
    early = DisclosureStub(1, AT, "X Portföy Fonu", "Portföy Dağılım Raporu")
    opaque = DisclosureStub(2, AT + MIN, "KAMUYU AYDINLATMA PLATFORMU", "Pay Alım Satım Bildirimi",
                            summary="Pay Alım Satım Bildirimi")
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])

    with open_archive(tmp_path / "strict.sqlite", writable=True) as archive:
        # Denetlenmemiş kurum satırı varken strict_watermark tarama saatini onun önüne çeker.
        r1 = asyncio.run(ingest_feed(
            ParseResult([early, opaque]), archive, resolver=resolver,
            probe_opaque=False, strict_watermark=True, clock=lambda: AT + 2 * MIN,
        ))
        assert r1.unprobed_opaque == 1
        assert r1.scanned_until == AT + MIN - timedelta(microseconds=1)
        assert archive.alarm_baglami("MTRKS", AT + 2 * MIN)["neden"] == "tarama_geride"

        # probe_opaque=True ile kurum satırının detayı açılır, MTRKS bulunur ve damga ilerler.
        async def fetch_insider(idx: int) -> DisclosureDetail:
            assert idx == 2
            return DisclosureDetail(
                DisclosureStub(2, AT + MIN, opaque.title, opaque.subject, disclosure_class="ODA", tickers=("MTRKS",)),
                f"<p>{insider_body}</p>", 0,
            )

        r2 = asyncio.run(ingest_feed(
            ParseResult([early, opaque]), archive, resolver=resolver,
            detail_fetcher=fetch_insider, probe_opaque=True, strict_watermark=True,
            clock=lambda: AT + 3 * MIN,
        ))
        assert r2.detail_requests == 1 and r2.counts["hazir"] == 1
        assert r2.scanned_until == AT + 3 * MIN
        ans = archive.alarm_baglami("MTRKS", AT + 3 * MIN)
        assert ans["durum"] == "var" and "%10,15" in ans["baglam"]["olay"]


def test_collector_service_company_cache_stale_fallback_warmup_and_loop(tmp_path) -> None:
    from bistman.collector import KapCollectorService

    company_html = (
        '<html><script>self.__next_f.push([1,"'
        '{\\"mkkMemberOid\\":\\"8acae2c575a1b5b60177de29c7177130\\",'
        '\\"kapMemberTitle\\":\\"MATR\\u0130KS F\\u0130NANSAL TEKNOLOJ\\u0130LER A.\\u015e.\\",'
        '\\"relatedMemberTitle\\":\\"\\",\\"stockCode\\":\\"MTRKS\\"}'
        '"])</script></html>'
    )
    history_bytes = (FIX / "member_history_mtrks.html").read_bytes()
    detail_bytes = (FIX / "detail_1699030_live.json").read_bytes()

    calls = {"companies": 0, "history": 0, "light": 0, "detail": 0}
    fail_companies = False
    now = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/tr/bist-sirketler":
            calls["companies"] += 1
            return httpx.Response(503) if fail_companies else httpx.Response(200, text=company_html)
        if path.endswith("/bildirim-sorgu-sonuc"):
            calls["history"] += 1
            return httpx.Response(200, content=history_bytes)
        if path.endswith("/list/light"):
            calls["light"] += 1
            return httpx.Response(200, json=[{
                "disclosureIndex": 1699030,
                "publishDate": "17.11.2026 14:53:07",
                "title": "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.",
                "subject": "Haber ve Söylentilere İlişkin Açıklama",
                "summary": "Haber ve Söylentilere İlişkin Açıklama",
            }])
        if path.endswith("/1699030"):
            calls["detail"] += 1
            return httpx.Response(200, content=detail_bytes)
        return httpx.Response(404)

    db = tmp_path / "render.sqlite"

    async def scenario() -> None:
        nonlocal fail_companies, now
        async with KapClient(transport=httpx.MockTransport(handler), max_attempts=1) as kap:
            svc = KapCollectorService(
                db, universe=["MTRKS", "THYAO"], kap=kap,
                clock=lambda: now,
            )
            # 1) Soğuk başlangıç ısınması: MTRKS geçmişini alır ama THYAO için sahte tarama damgası yazmaz.
            w = await svc.warmup(["MTRKS"], max_age=timedelta(days=2))
            assert w.counts["hazir"] == 1 and calls["companies"] == 1 and calls["history"] == 1
            with open_archive(db) as reader:
                assert reader.alarm_baglami("MTRKS", now)["durum"] == "var"
                assert reader.alarm_baglami("THYAO", now)["neden"] == "tarama_kaydi_yok"

            # 2) İlk canlı tur: şirket listesi SQLite önbelleğinden okunur (0 ek şirket isteği);
            #    ısınmada kilitlenen `known_at` (now) değişmez.
            t_warmup = now
            now = now + MIN
            r1 = await svc.run_once()
            assert calls["companies"] == 1 and r1.scanned_until == now
            with open_archive(db) as reader:
                assert reader.alarm_baglami("MTRKS", t_warmup)["durum"] == "var"

            # 3) Sonraki turda aynı /list/light satırı için 0 detay isteği yapılır;
            #    25 saat sonra /tr/bist-sirketler 503 verse bile bayat önbellekle tarama kesilmez.
            fail_companies = True
            now = now + timedelta(hours=25)
            r2 = await svc.run_once()
            assert calls["companies"] == 2 and r2.detail_requests == 0 and r2.scanned_until == now

            # 4) run_forever stop_event ile temizce durur.
            stop = asyncio.Event()

            async def trigger_stop() -> None:
                await asyncio.sleep(0.01)
                stop.set()

            stopper = asyncio.create_task(trigger_stop())
            await svc.run_forever(interval_seconds=60.0, stop_event=stop)
            await stopper

    asyncio.run(scenario())
