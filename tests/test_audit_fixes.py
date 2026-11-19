"""Tam sistem denetiminde bulunan tüm maddelerin uçtan uca regresyon testleri."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bistman.alarm_context import (
    AlarmArchive,
    kap_baglami_ekle,
    open_archive,
)
from bistman.collector import (
    KapCollectorService,
    extract_verified_evidence,
    ingest_feed,
)
from bistman.context_packet import build_packet, make_candidate
from bistman.fundamentals import FundamentalRow, PublishedRow
from bistman.parsing.dates import TRT
from bistman.parsing.evidence import sale_qualified
from bistman.parsing.kap_table import HistoryRow, parse_when
from bistman.parsing.money import find_amounts
from bistman.parsing.text import tr_lower
from bistman.sources.base import DisclosureDetail, DisclosureStub, ParseResult
from bistman.sources.kap_companies import (
    CompanyEntry,
    build_company_resolver,
    parse_companies,
    select_primary_ticker,
)
from bistman.ticker_map import TickerResolver
from bistman.triage import Route, route

AT = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


# --- 1) Borsada işlem görmeyen / kardeş ihraççı atıf koruması + detay OID denetimi ---


def test_unlisted_brokers_and_banks_never_fuzzy_match_bist_issuers() -> None:
    resolver = TickerResolver.from_pairs([
        ("ISYAT", "İŞ YATIRIM ORTAKLIĞI A.Ş."),
        ("ISMEN", "İŞ YATIRIM MENKUL DEĞERLER A.Ş."),
        ("ISCTR", "TÜRKİYE İŞ BANKASI A.Ş."),
        ("AKBNK", "AKBANK T.A.Ş."),
        ("GARAN", "TÜRKİYE GARANTİ BANKASI A.Ş."),
        ("HALKB", "TÜRKİYE HALK BANKASI A.Ş."),
        ("VAKBN", "TÜRKİYE VAKIFLAR BANKASI T.A.O."),
        ("THYAO", "TÜRK HAVA YOLLARI A.O."),
        ("ASELS", "ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş."),
    ])

    unlisted_or_sibling = (
        "AK YATIRIM MENKUL DEĞERLER A.Ş.",
        "GARANTİ YATIRIM MENKUL KIYMETLER A.Ş.",
        "HALK YATIRIM MENKUL DEĞERLER A.Ş.",
        "VAKIF YATIRIM MENKUL DEĞERLER A.Ş.",
        "ZİRAAT YATIRIM MENKUL DEĞERLER A.Ş.",
        "TÜRKİYE CUMHURİYETİ ZİRAAT BANKASI A.Ş.",
        "TÜRK EXİMBANK A.Ş.",
        "AK FAKTORİNG A.Ş.",
        "AK PORTFÖY YÖNETİMİ A.Ş.",
        "DENİZ YATIRIM MENKUL KIYMETLER A.Ş.",
    )
    for title in unlisted_or_sibling:
        assert resolver.resolve(title).ticker is None, f"yanlış atıf: {title}"

    # Gerçek BIST şirketlerinin tam, ekli ve 1 harf hatalı yazımları çalışmaya devam eder.
    assert resolver.resolve("TÜRKİYE VAKIFLAR BANKASI T.A.O.").ticker == "VAKBN"
    assert resolver.resolve("AKBANK T.A.Ş.").ticker == "AKBNK"
    assert resolver.resolve("İŞ YATIRIM MENKUL DEĞERLER A.Ş.").ticker == "ISMEN"
    assert resolver.resolve("ASELSAN ELEKTRONİK SANAYİ VE TİCARET ANONİM ŞİRKETİ").ticker == "ASELS"
    assert resolver.resolve("ASELSAN ELEKTRONIK SANAYI VE TICARET A.Ş.").ticker == "ASELS"
    assert resolver.resolve("ASELSAN ELEKTRONİK SANAYİİ VE TİCARET A.Ş.").ticker == "ASELS"


def test_detail_non_bist_member_oid_blocks_and_retracts_attribution(tmp_path) -> None:
    companies = [CompanyEntry("ORNEK", "oid-bist-ornek", "ÖRNEK SANAYİ A.Ş.")]
    resolver = build_company_resolver(companies)
    body = "Şirketimiz ile ABC A.Ş. arasında 50 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    # Unvan aynı/çok benzer görünse bile detaydaki mkkMemberOid BIST listesinde değilse elenir.
    stub = DisclosureStub(10, AT, "ÖRNEK SANAYİ A.Ş.", "Yeni İş İlişkisi", summary=body)

    async def unlisted_detail(_: int) -> DisclosureDetail:
        return DisclosureDetail(
            DisclosureStub(
                10, AT, "ÖRNEK SANAYİ A.Ş.", "Yeni İş İlişkisi",
                disclosure_class="ODA", tickers=(), member_oid="oid-unlisted-bond-issuer",
            ),
            f"<p>{body}</p>",
            0,
        )

    with open_archive(tmp_path / "oid.sqlite", writable=True) as archive:
        # Önce detaysız özet yazılmış olsa bile detay geldiğinde geri çekilir.
        r1 = asyncio.run(ingest_feed(
            ParseResult([stub]), archive, resolver=resolver, companies=companies, clock=lambda: AT,
        ))
        assert r1.counts["hazir"] == 1
        updated = DisclosureStub(10, AT, stub.title, stub.subject, summary=body + " ")
        r2 = asyncio.run(ingest_feed(
            ParseResult([updated]), archive, resolver=resolver, companies=companies,
            detail_fetcher=unlisted_detail, clock=lambda: AT + MIN,
        ))
        assert r2.counts["geri_cekildi"] == 1
        assert archive.alarm_baglami("ORNEK", AT + MIN)["durum"] == "yok"


# --- 2) Aynı OID'li çok kodlu BIST şirketleri (GARAN/TGB, ISCTR, KRDMD) ---


def test_multi_code_same_oid_bist_giants_resolve_cleanly_while_multi_company_rejected(tmp_path) -> None:
    companies = [
        CompanyEntry("GARAN", "oid-garan", "TÜRKİYE GARANTİ BANKASI A.Ş."),
        CompanyEntry("TGB", "oid-garan", "TÜRKİYE GARANTİ BANKASI A.Ş."),
        CompanyEntry("THB", "oid-halk", "TÜRKİYE HALK BANKASI A.Ş."),
        CompanyEntry("HALKB", "oid-halk", "TÜRKİYE HALK BANKASI A.Ş."),
        CompanyEntry("ISA", "oid-is", "TÜRKİYE İŞ BANKASI A.Ş."),
        CompanyEntry("ISB", "oid-is", "TÜRKİYE İŞ BANKASI A.Ş."),
        CompanyEntry("ISCTR", "oid-is", "TÜRKİYE İŞ BANKASI A.Ş."),
        CompanyEntry("ISKUR", "oid-is", "TÜRKİYE İŞ BANKASI A.Ş."),
        CompanyEntry("KRDMA", "oid-krd", "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş."),
        CompanyEntry("KRDMB", "oid-krd", "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş."),
        CompanyEntry("KRDMD", "oid-krd", "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş."),
        CompanyEntry("ASELS", "oid-asels", "ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş."),
    ]
    assert select_primary_ticker(["TGB", "GARAN"]) == "GARAN"
    assert select_primary_ticker(["THB", "HALKB"]) == "HALKB"
    assert select_primary_ticker(["ISA", "ISB", "ISCTR", "ISKUR"]) == "ISCTR"
    assert select_primary_ticker(["KRDMA", "KRDMB", "KRDMD"]) == "KRDMD"
    assert select_primary_ticker(["KRDMA", "KRDMB", "KRDMD"], allowed={"KRDMA"}) == "KRDMA"

    resolver = build_company_resolver(companies)
    assert resolver.resolve("TÜRKİYE İŞ BANKASI A.Ş.").ticker == "ISCTR"
    assert resolver.resolve("TÜRKİYE HALK BANKASI A.Ş.").ticker == "HALKB"
    assert resolver.resolve("KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş.").ticker == "KRDMD"

    # Kullanıcı özellikle KRDMA izliyorsa preferred ile KRDMA seçilir, diğer şirketler de korunur.
    pref = build_company_resolver(companies, preferred=["KRDMA"])
    assert pref.resolve("KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş.").ticker == "KRDMA"
    assert pref.resolve("ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş.").ticker == "ASELS"

    div_text = "Pay başına brüt 3,20 TL nakit kar payı dağıtılmasına karar verdi."
    sale_text = "Şirketimiz ile XYZ A.Ş. arasında 400 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    s_garan = DisclosureStub(1, AT, "TÜRKİYE GARANTİ BANKASI A.Ş.", "Kar Payı Dağıtım İşlemlerine İlişkin Bildirim")
    s_isctr = DisclosureStub(2, AT + MIN, "TÜRKİYE İŞ BANKASI A.Ş.", "Kar Payı Dağıtım İşlemlerine İlişkin Bildirim")
    s_krdma = DisclosureStub(3, AT + 2 * MIN, "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş.", "Yeni İş İlişkisi")
    s_multi = DisclosureStub(4, AT + 3 * MIN, "ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş.", "Yeni İş İlişkisi")

    details = {
        1: DisclosureDetail(
            DisclosureStub(1, AT, s_garan.title, s_garan.subject, disclosure_class="ODA",
                           tickers=("GARAN", "TGB"), member_oid="oid-garan"),
            f"<p>{div_text}</p>", 0,
        ),
        2: DisclosureDetail(
            DisclosureStub(2, AT + MIN, s_isctr.title, s_isctr.subject, disclosure_class="ODA",
                           tickers=("ISA", "ISB", "ISCTR", "ISKUR"), member_oid="oid-is"),
            f"<p>{div_text}</p>", 0,
        ),
        3: DisclosureDetail(
            DisclosureStub(3, AT + 2 * MIN, s_krdma.title, s_krdma.subject, disclosure_class="ODA",
                           tickers=("KRDMA", "KRDMB", "KRDMD"), member_oid="oid-krd"),
            f"<p>{sale_text}</p>", 0,
        ),
        # İki FARKLI şirket OID'i -> kesin olarak cok_hisseli kalmalı!
        4: DisclosureDetail(
            DisclosureStub(4, AT + 3 * MIN, s_multi.title, s_multi.subject, disclosure_class="ODA",
                           tickers=("ASELS", "GARAN")),
            f"<p>{sale_text}</p>", 0,
        ),
    }

    async def fetcher(idx: int) -> DisclosureDetail:
        return details[idx]

    watched = ("GARAN", "ISCTR", "KRDMA", "ASELS")
    with open_archive(tmp_path / "multicode.sqlite", writable=True) as archive:
        rep = asyncio.run(ingest_feed(
            ParseResult([s_garan, s_isctr, s_krdma, s_multi]),
            archive,
            resolver=build_company_resolver(companies, preferred=watched),
            detail_fetcher=fetcher,
            companies=companies,
            universe=watched,
            clock=lambda: AT + 5 * MIN,
        ))
        assert rep.counts["hazir"] == 3
        assert rep.counts["cok_hisseli"] == 1
        assert archive.alarm_baglami("GARAN", AT + 5 * MIN)["durum"] == "var"
        assert archive.alarm_baglami("ISCTR", AT + 5 * MIN)["durum"] == "var"
        assert archive.alarm_baglami("KRDMA", AT + 5 * MIN)["durum"] == "var"
        assert archive.alarm_baglami("ASELS", AT + 5 * MIN)["durum"] == "yok"


def test_collector_service_warmup_deduplicates_same_oid_and_tolerates_small_skew(tmp_path) -> None:
    companies = [
        CompanyEntry("KRDMA", "oid-krd", "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş."),
        CompanyEntry("KRDMD", "oid-krd", "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş."),
    ]
    history_calls: list[str] = []
    # Saat 11:00:00 TRT (08:00:00 UTC) iken 11:00:20 TRT'de yayımlanmış bildirim (20 sn saat kayması).
    pub_utc = AT + timedelta(seconds=20)
    sale = "Şirketimiz ile DEF A.Ş. arasında 120 milyon TL bedelli satış sözleşmesi imzalanmıştır."

    class FakeKap:
        async def fetch_history(self, oid: str, *, today: date | None = None) -> list[HistoryRow]:
            history_calls.append(oid)
            return [HistoryRow(
                index=77,
                published_at=datetime(2026, 11, 18, 11, 0),
                disclosure_class="ODA",
                company_title="KARDEMİR A.Ş.",
                subject="Yeni İş İlişkisi",
                summary=sale,
                tickers=("KRDMA", "KRDMD"),
            )]

        async def detail(self, idx: int) -> DisclosureDetail:
            assert idx == 77
            return DisclosureDetail(
                DisclosureStub(
                    77, pub_utc, "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş.",
                    "Yeni İş İlişkisi", disclosure_class="ODA",
                    tickers=("KRDMA", "KRDMD"), member_oid="oid-krd",
                ),
                f"<p>{sale}</p>", 0,
            )

    db = tmp_path / "svc.sqlite"
    with open_archive(db, writable=True) as a:
        a.save_companies(companies, updated_at=AT)

    svc = KapCollectorService(db, universe=("KRDMA", "KRDMD"), kap=FakeKap(), clock=lambda: AT)  # type: ignore[arg-type]
    rep = asyncio.run(svc.warmup())
    assert history_calls == ["oid-krd"]  # aynı OID için tek sorgu
    assert rep.counts["hazir"] == 1
    with open_archive(db) as a:
        # Zero-lookahead korunur: 08:00:00'da yok, 08:00:20'de var!
        assert a.alarm_baglami("KRDMD", AT)["durum"] == "yok"
        assert a.alarm_baglami("KRDMD", pub_utc)["durum"] == "var"


# --- 3) Büyük gövde performansı: 50+ KB bildirimde O(N) milisaniye çalışma ---


def test_large_50kb_disclosure_runs_in_milliseconds_not_minute() -> None:
    filler = "Şirketimiz Prof. Dr. Ahmet Yılmaz başkanlığında olağan idari değerlendirmesini sürdürmektedir."
    target = "Şirketimiz ile XYZ San. ve Tic. A.Ş. arasında 250 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    big_body = "\n".join([filler] * 600 + [target] + [filler] * 100)
    assert len(big_body.encode("utf-8")) > 55_000

    stub = DisclosureStub(900, AT, "Örnek A.Ş.", "Özel Durum Açıklaması (Genel)", disclosure_class="ODA", tickers=("ORNEK",))
    t0 = time.perf_counter()
    prop = extract_verified_evidence(stub, big_body)
    pkt = build_packet(
        make_candidate(stub, "ORNEK"),
        origin="kap_arsiv",
        topic="satis_sozlesmesi",
        quote=target,
        detail=DisclosureDetail(stub, f"<p>{big_body}</p>", 0),
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert prop is not None and prop.quote == target
    assert pkt["kanit_yeri"] == "govde"
    # Eski O(N²) davranışta ~50.000 ms sürüyordu; O(N) sınır denetimiyle < 400 ms olmalı.
    assert elapsed_ms < 400, f"büyük gövde çok yavaş: {elapsed_ms:.1f} ms"

    # Kısaltma sonrası parçalı alıntı reddi büyük gövdede de birebir korunur.
    with pytest.raises(ValueError, match="alinti_cumle_sinirinda_degil"):
        build_packet(
            make_candidate(stub, "ORNEK"),
            origin="kap_arsiv",
            topic="sirket_aciklamasi",
            quote="Ahmet Yılmaz başkanlığında olağan idari değerlendirmesini sürdürmektedir.",
            detail=DisclosureDetail(stub, f"<p>{big_body}</p>", 0),
        )


# --- 4) parse_when varsayılan bugün tarihi TRT olmalı ---


def test_parse_when_defaults_to_trt_not_host_utc(monkeypatch) -> None:
    import bistman.parsing.kap_table as kap_table_mod

    # UTC 22:30 (2026-11-17) == TRT 01:30 (2026-11-18): "Bugün 00:45" -> 18 Kasım olmalı.
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)

    monkeypatch.setattr(kap_table_mod, "datetime", FakeDateTime)
    parsed = parse_when("Bugün 00:45")
    assert parsed == datetime(2026, 11, 18, 0, 45)
    assert parsed.replace(tzinfo=TRT).date() == date(2026, 11, 18)


# --- 5) UNRELIABLE_TTM bayrakları + çok küçük oranda "%0" yerine "%0,01'den düşük" ---


@pytest.mark.parametrize("bad_flag", ["data_gap", "unit_mismatch"])
def test_unreliable_ttm_flags_suppress_revenue_ratio(bad_flag: str) -> None:
    sale = "Şirketimiz ile KLM A.Ş. arasında 50 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    stub = DisclosureStub(20, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=sale, tickers=("ORNEK",))
    row = PublishedRow(
        FundamentalRow("ORNEK", "2026-09-30", "TRY", "industrial", revenue_ttm=1_000_000_000.0,
                       source="kap_fr", flags=(bad_flag,)),
        AT - timedelta(days=10),
    )
    pkt = build_packet(
        make_candidate(stub, "ORNEK"), origin="kap_arsiv",
        topic="satis_sozlesmesi", quote=sale, statements=(row,),
    )
    assert "olcek" not in pkt
    assert pkt["sinir"] == "karsilastirilabilir_ttm_ciro_yok"


def test_tiny_positive_ratio_never_renders_as_zero_percent(tmp_path) -> None:
    sale = "Şirketimiz ile KLM A.Ş. arasında 1.000.000 TL bedelli satış sözleşmesi imzalanmıştır."
    stub = DisclosureStub(21, AT, "Dev Sanayi A.Ş.", "Yeni İş İlişkisi", summary=sale, tickers=("DEVSA",))
    # 1 milyon TL / 50 milyar TL = %0.002 (< %0.005)
    row = PublishedRow(
        FundamentalRow("DEVSA", "2026-09-30", "TRY", "industrial", revenue_ttm=50_000_000_000.0, source="kap_fr"),
        AT - timedelta(days=10),
    )
    with open_archive(tmp_path / "tiny.sqlite", writable=True) as archive:
        out = archive.record(
            make_candidate(stub, "DEVSA"), origin="kap_arsiv", known_at=AT,
            topic="satis_sozlesmesi", quote=sale, statements=(row,),
        )
        assert out == "hazir"
        ans = archive.alarm_baglami("DEVSA", AT)
        assert ans["durum"] == "var"
        assert "%0,01'den düşük" in ans["baglam"]["onemi"]
        assert "%0;" not in ans["baglam"]["onemi"]


# --- 6) Sözleşme feshi, opsiyon şartı, olumsuzlanmış ceza, rutin geri alım ve para ayrıştırıcı ---


def test_contract_termination_captured_verbatim_and_supersedes_prior_sale_on_same_id(tmp_path) -> None:
    signed = "Şirketimiz ile ABC A.Ş. arasında 200 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    terminated = (
        "Şirketimiz ile ABC A.Ş. arasında imzalanan 200 milyon TL bedelli satış sözleşmesi "
        "karşılıklı mutabakat ile feshedilmiştir."
    )
    s1 = DisclosureStub(30, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=signed, tickers=("ORNEK",))
    s2 = DisclosureStub(30, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=terminated, tickers=("ORNEK",))
    resolver = TickerResolver.from_pairs([("ORNEK", "Örnek A.Ş.")])

    with open_archive(tmp_path / "term.sqlite", writable=True) as archive:
        asyncio.run(ingest_feed(ParseResult([s1]), archive, resolver=resolver, clock=lambda: AT))
        a1 = archive.alarm_baglami("ORNEK", AT)
        assert a1["durum"] == "var" and a1["baglam"]["konu"] == "satis_sozlesmesi"

        asyncio.run(ingest_feed(ParseResult([s2]), archive, resolver=resolver, clock=lambda: AT + MIN))
        # Eski an değişmez; güncelleme anından itibaren fesih cümlesi döner (olumlu satış gibi değil).
        assert archive.alarm_baglami("ORNEK", AT)["baglam"]["konu"] == "satis_sozlesmesi"
        a2 = archive.alarm_baglami("ORNEK", AT + MIN)
        assert a2["durum"] == "var"
        assert a2["baglam"]["konu"] == "sirket_aciklamasi"
        assert a2["baglam"]["olay"] == terminated


def test_option_conditionality_negated_fines_daily_buyback_and_money_guards(tmp_path) -> None:
    # Opsiyona bağlı sözleşme kesin satış sayılmaz.
    opt = "Şirketimiz ile XYZ A.Ş. arasında alıcının opsiyonuna bağlı 80 milyon TL tutarında sözleşme imzalanmıştır."
    assert sale_qualified(opt) is True
    prop = extract_verified_evidence(
        DisclosureStub(31, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", tickers=("ORNEK",)), opt,
    )
    # Aday satis_sozlesmesi olarak dönse bile archive.record satis_kaniti_yetersiz ile bekletir.
    with open_archive(tmp_path / "opt.sqlite", writable=True) as db:
        assert db.record(
            make_candidate(DisclosureStub(31, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=opt, tickers=("ORNEK",)), "ORNEK"),
            origin="kap_arsiv", known_at=AT, topic=prop.topic if prop else "satis_sozlesmesi", quote=opt,
        ) == "satis_kaniti_yetersiz"

    # Olumsuzlanmış ceza, karar ve plan cümleleri olumlu gelişme gibi seçilmez.
    for subj, neg_body in (
        ("İdari Para Cezası", "Şirketimiz hakkında herhangi bir idari para cezası uygulanmamıştır."),
        ("Sermaye Artırımı", "Yönetim Kurulumuz bedelsiz sermaye artırımı yapılmasına karar vermemiştir."),
        ("Payların Geri Alınmasına İlişkin Bildirim", "Pay geri alım programı başlatılmamasına karar verilmiştir."),
        ("Özel Durum Açıklaması (Genel)", "Yeni üretim tesisinin 2028 yılında devreye alınması planlanmaktadır."),
        # Önceki YK kararına atıflı günlük rutin geri alım işlemi program kararı sanılmaz.
        ("Payların Geri Alınmasına İlişkin Bildirim",
         "Yönetim Kurulumuzun geri alım kararı kapsamında borsadan 10.000 adet pay geri alınmıştır."),
    ):
        s = DisclosureStub(32, AT, "Örnek A.Ş.", subj, disclosure_class="ODA", tickers=("ORNEK",))
        assert extract_verified_evidence(s, neg_body) is None, neg_body

    # Negatif tutar pozitif para sanılmaz; aralıklı kısa çizgi (nesir) korunur.
    assert find_amounts("Dönem net zararı -50.000.000 TL olarak gerçekleşmiştir.") == []
    assert find_amounts("Net değişim −12,5 milyon USD olmuştur.") == []
    prose = find_amounts("Sözleşme bedeli - 50 milyon TL - peşin tahsil edilecektir.")
    assert len(prose) == 1 and prose[0].amount == Decimal("50000000")

    # Belirsiz İngiliz binlik virgülü ("1,250 TL") uyarılır ve ciro oranı uydurulmaz.
    amb_sale = "Şirketimiz ile ABC A.Ş. arasında 1,250 TL bedelli satış sözleşmesi imzalanmıştır."
    amb = find_amounts(amb_sale)
    assert len(amb) == 1 and any(w.startswith("belirsiz_virgul_binlik:") for w in amb[0].warnings)
    row = PublishedRow(
        FundamentalRow("ORNEK", "2026-09-30", "TRY", "industrial", revenue_ttm=10_000_000.0, source="kap_fr"),
        AT - timedelta(days=5),
    )
    pkt = build_packet(
        make_candidate(DisclosureStub(33, AT, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=amb_sale, tickers=("ORNEK",)), "ORNEK"),
        origin="kap_arsiv", topic="satis_sozlesmesi", quote=amb_sale, statements=(row,),
    )
    assert "olcek" not in pkt and pkt["sinir"] == "tutar_bicimi_belirsiz"


# --- 7) DKB VBTS/brüt takas/pay sırası triyajı + etken çatı ve yeni BIST katalizörleri ---


def test_dkb_vbts_halt_triage_and_active_voice_bist_catalysts() -> None:
    for dkb_subj in (
        "Volatilite Bazlı Tedbir Sistemi (VBTS) Kapsamında Alınan Tedbirler",
        "Pay Sırasının İşleme Kapatılması",
        "Pay Sırasının Yeniden İşleme Açılması",
        "Brüt Takas Uygulaması",
        "İşlem Sırasının Durdurulması",
    ):
        assert route("DKB", dkb_subj) is Route.PIPELINE, dkb_subj

    # Devre kesici mekanik kalmaya devam eder.
    assert route("DKB", "Pay Piyasasında Volatilite Bazlı Devre Kesici Uygulaması") is Route.MECHANICAL

    cases = (
        ("Sermaye Artırımı", "Yönetim Kurulumuz çıkarılmış sermayenin %150 bedelsiz artırılmasına karar verdi."),
        ("Payların Geri Alınmasına İlişkin Bildirim", "Yönetim Kurulumuz pay geri alım programı başlatılmasına karar vermiştir."),
        ("VBTS Tedbiri", "ORNEK.E payları 17/12/2026 seans sonuna kadar açığa satışa ve kredili işlemlere konu edilemeyecektir."),
        ("Pay Sırasının İşleme Kapatılması", "ORNEK.E pay sırası geçici olarak işleme kapatılmıştır."),
        ("Özel Durum Açıklaması (Genel)", "Sendika tarafından işyerlerimizde grev kararı alınmıştır."),
        ("Özel Durum Açıklaması (Genel)", "Şirketimizin elektrik üretim lisansı iptal edilmiştir."),
        ("Özel Durum Açıklaması (Genel)", "Şirketimiz konkordato talebinde bulunmuştur."),
        ("Özel Durum Açıklaması (Genel)", "Yeni güneş enerjisi üretim tesisi bugün itibarıyla devreye alınmıştır."),
        ("Özel Durum Açıklaması (Genel)", "Şirketimiz 2026 yılı hasılat beklentisini yukarı yönlü revize etmiştir."),
        ("Kredi Derecelendirmesi", "Fitch Ratings uzun vadeli ulusal kredi derecelendirme notunu A (tur) seviyesine düşürülmüştür."),
    )
    for subj, sentence in cases:
        stub = DisclosureStub(40, AT, "Örnek A.Ş.", subj, disclosure_class="ODA", tickers=("ORNEK",))
        prop = extract_verified_evidence(stub, sentence)
        assert prop is not None and prop.quote == sentence, sentence


# --- 8) Unicode I\u0307 / i\u0307 normalleştirmesi ---


def test_tr_lower_eliminates_combining_dot_above() -> None:
    assert tr_lower("I\u0307ŞLEM YASAĞI") == "işlem yasağı"
    assert tr_lower("i\u0307hale süreci") == "ihale süreci"
    assert "\u0307" not in tr_lower("İSTANBUL I\u0307ZMİR i\u0307ÇEL")
    assert route("DKB", "I\u0307şlem Yasağı Nedeniyle Pay Duyurusu") is Route.PIPELINE


# --- 9) İki Next.js `__next_f.push` parçasına bölünmüş KAP şirket kaydı ---


def test_parse_companies_reassembles_split_nextjs_flight_chunks() -> None:
    split_html = (
        '<html><script>self.__next_f.push([1,"'
        '[{\\"mkkMemberOid\\":\\"oid-asels\\",\\"kapMemberTitle\\":\\"ASELSAN ELEK'
        '"])</script><script>self.__next_f.push([1,"'
        'TRON\\u0130K SANAY\\u0130 VE T\\u0130CARET A.\\u015e.\\",'
        '\\"relatedMemberTitle\\":\\"\\",\\"stockCode\\":\\"ASELS\\"},'
        '{\\"mkkMemberOid\\":\\"oid-is\\",\\"kapMemberTitle\\":\\"T\\u00dcRK\\u0130YE \\u0130\\u015e BANKASI A.\\u015e.\\",'
        '\\"relatedMemberTitle\\":\\"\\",\\"stockCode\\":\\"ISA, IS"'
        '])</script><script>self.__next_f.push([1,'
        '"B, ISCTR, ISKUR\\"}]"])</script></html>'
    )
    entries = parse_companies(split_html)
    by_code = {e.ticker: e for e in entries}
    assert set(by_code) == {"ASELS", "ISA", "ISB", "ISCTR", "ISKUR"}
    assert by_code["ASELS"].title == "ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş."
    assert by_code["ISCTR"].oid == "oid-is"


# --- 10) alarm_context uç durumları (prune, sözlük yeniden kullanımı, .E/.IS, minute_pub) ---


def test_alarm_context_prune_reused_dict_ticker_suffix_and_deferred_minute_pub(tmp_path) -> None:
    db_path = tmp_path / "edge.sqlite"
    sale = "Şirketimiz ile ABC A.Ş. arasında 90 milyon TL bedelli satış sözleşmesi imzalanmıştır."
    corr = "Şirketimiz ile ABC A.Ş. arasında 75 milyon TL bedelli revize satış sözleşmesi imzalanmıştır."

    with open_archive(db_path, writable=True) as archive:
        # 1) BIST:MTRKS, MTRKS.E, MTRKS.IS, A1CAP geçerli; XU100 ve THYAO1 reddedilir.
        archive.record_scan(AT + 10 * MIN, observed_at=AT + 10 * MIN)
        for valid in ("MTRKS", "mtrks", "BIST:MTRKS", "MTRKS.E", "MTRKS.IS", "BIST:MTRKS.E", "A1CAP"):
            assert archive.alarm_baglami(valid, AT + 5 * MIN)["durum"] == "yok"
        for invalid in ("XU100", "THYAO1", "MTRKS.E.E", "AB", "ABCDEF", "MTRKS,ASELS"):
            with pytest.raises(ValueError):
                archive.alarm_baglami(invalid, AT + 5 * MIN)

        # 2) prune(): daha yeni observed ile eski bir covered yazılmış olsa bile max(covered) korunur.
        old = AT - timedelta(days=30)
        archive.record_scan(old + 20 * MIN, observed_at=old + 20 * MIN, origin="sentetik")  # en yüksek covered
        archive.record_scan(old + 5 * MIN, observed_at=old + 25 * MIN, origin="sentetik")   # en yüksek observed, düşük covered
        archive.prune(as_of=AT + 30 * MIN, seen_days=7)
        assert archive.last_scan(origin="sentetik") == old + 20 * MIN

        # 3) Sırasız gelen düzeltmede minute_precision bilgisi hedef gelince kaybolmaz.
        rep_stub = DisclosureStub(
            101, AT + 2 * MIN, "Matriks A.Ş.", "Yeni İş İlişkisi",
            disclosure_class="ODA", tickers=("MTRKS",), minute_precision=True,
        )
        rep_det = DisclosureDetail(rep_stub, f"<p>{corr}</p>", 0)
        st = archive.record(
            make_candidate(rep_stub, "MTRKS"), origin="kap_arsiv", known_at=AT + 3 * MIN,
            topic="satis_sozlesmesi", quote=corr, detail=rep_det,
            supersedes=100, defer_missing_target=True,
        )
        assert st == "hedef_bekleniyor"

        tgt_stub = DisclosureStub(
            100, AT, "Matriks A.Ş.", "Yeni İş İlişkisi",
            disclosure_class="ODA", tickers=("MTRKS",),
        )
        tgt_det = DisclosureDetail(tgt_stub, f"<p>{sale}</p>", 0)
        assert archive.record(
            make_candidate(tgt_stub, "MTRKS"), origin="kap_arsiv", known_at=AT + 4 * MIN,
            topic="satis_sozlesmesi", quote=sale, detail=tgt_det,
        ) == "hazir"

        # 101'in minute_pub=1 korunduğunu doğrula.
        row = archive._db.execute(
            "SELECT minute_pub FROM alarm_versions WHERE disclosure_id=101"
        ).fetchone()
        assert row == (1,)

    # 4) kap_baglami_ekle yeniden kullanılan istek sözlüğünde eski hissenin bağlamını bırakmaz.
    payload: dict[str, object] = {"grafik": "kırılım"}
    r_hit = kap_baglami_ekle(payload, db_path, "MTRKS.E", AT + 5 * MIN)
    assert r_hit is not None and r_hit["durum"] == "var" and "kap_baglami" in payload

    r_miss = kap_baglami_ekle(payload, db_path, "ASELS.IS", AT + 5 * MIN)
    assert r_miss is not None and r_miss["durum"] == "yok"
    assert "kap_baglami" not in payload and "kap_bekleyen_bildirim" not in payload
    assert payload == {"grafik": "kırılım"}

    # Hata durumunda da eski kap_baglami temizlenir.
    kap_baglami_ekle(payload, db_path, "MTRKS", AT + 5 * MIN)
    assert "kap_baglami" in payload
    r_err = kap_baglami_ekle(payload, db_path, "XU100", AT + 5 * MIN)
    assert r_err is None and "kap_baglami" not in payload

    # 5) Eski şemadan (minute_pub sütunsuz alarm_pending_replacements) sorunsuz yükseltme.
    legacy = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(legacy)
    con.executescript("""
        CREATE TABLE alarm_versions (
            origin TEXT NOT NULL, disclosure_id INTEGER NOT NULL, version BLOB NOT NULL,
            ticker TEXT NOT NULL, published TEXT NOT NULL, known TEXT NOT NULL,
            strength INTEGER NOT NULL, live INTEGER NOT NULL DEFAULT 1,
            minute_pub INTEGER NOT NULL DEFAULT 0, source_hash BLOB NOT NULL,
            valid_until TEXT, evidence TEXT, answer TEXT, held TEXT,
            PRIMARY KEY (origin, disclosure_id, version)
        );
        CREATE TABLE alarm_replacements (
            origin TEXT NOT NULL, target INTEGER NOT NULL, replacement INTEGER NOT NULL,
            known TEXT NOT NULL, live INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (origin, target, replacement)
        );
        CREATE TABLE alarm_pending_replacements (
            origin TEXT NOT NULL, target INTEGER NOT NULL, replacement INTEGER NOT NULL,
            version BLOB NOT NULL, ticker TEXT NOT NULL, published TEXT NOT NULL,
            known TEXT NOT NULL, live INTEGER NOT NULL, source_hash BLOB NOT NULL,
            valid_until TEXT, evidence TEXT NOT NULL, answer TEXT NOT NULL,
            PRIMARY KEY (origin, target, replacement, version)
        );
    """)
    con.close()
    with open_archive(legacy, writable=True) as migrated:
        cols = {r[1] for r in migrated._db.execute("PRAGMA table_info(alarm_pending_replacements)").fetchall()}
        assert "minute_pub" in cols

