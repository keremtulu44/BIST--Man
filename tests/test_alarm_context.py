"""Dış alarm → kayıtlı kanıt. Ne skor ne de haberin gelişi tetikleyicidir."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bistman.alarm_context import ReviewedReading, open_archive
from bistman.fundamentals import PublishedRow
from bistman.parsing.dates import TRT
from bistman.pipeline import ProcessedDisclosure
from bistman.sources.base import DisclosureDetail, DisclosureStub, FundamentalRow
from bistman.triage import Route

AT = datetime(2026, 11, 17, 10, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
SEC = timedelta(seconds=1)
SALE = "Şirketimiz 60 milyon TL bedelli satış sözleşmesi imzalamıştır."


def item(idx=1, ticker="DENEM", at=AT, text=SALE):
    stub = DisclosureStub(idx, at, "Deneme A.Ş.", "Yeni Sözleşme", summary=text, tickers=(ticker,))
    # Gönderim kararı/skoru/bilançosu YOK, ama doğrudan şirket kanıtı var.
    return ProcessedDisclosure(stub, Route.PIPELINE, "no_fundamentals", ticker=ticker)


def save(archive, row, *, known=None, body=None, **kw):
    return archive.record(row, topic="satis_sozlesmesi", origin="sentetik", quote=SALE,
                          known_at=known or row.disclosure.published_at,
                          detail=DisclosureDetail(row.disclosure, body, 0) if body is not None else None, **kw)


def lookup(archive, at=AT, ticker="DENEM", **kw):
    return archive.alarm_baglami(ticker, at, origin="sentetik", **kw)


@pytest.fixture
def archive(tmp_path):
    with open_archive(tmp_path / "haber.sqlite", writable=True) as store:
        yield store


def test_only_latest_meaningful_item_for_that_stock(archive):
    old, new = item(), item(2, at=AT + HOUR)
    save(archive, new)
    save(archive, old)
    save(archive, item(3, "DIGER", AT + 2 * HOUR))
    routine = replace(item(4, at=AT + 2 * HOUR), route=Route.ROUTINE, status="filtered_routine")
    assert save(archive, routine) == "kapsam_disi"
    result = lookup(archive, (AT + 2 * HOUR).astimezone(TRT), " bist:denem ")
    assert result["hisse"] == "DENEM" and result["durum"] == "var"
    assert result["baglam"]["kaynak"] == "senaryo:2"
    assert result["baglam"]["olay"] == SALE
    assert result["baglam"]["onemi"] and "skor" not in result["baglam"]
    # Sentetik kanıt, gerçek sorguya varsayılan olarak karışmaz.
    assert archive.alarm_baglami("DENEM", AT)["baglam"] is None


def test_publication_and_first_usable_version_have_separate_clocks(archive):
    save(archive, item(), known=AT + HOUR)
    for alarm in (AT - SEC, AT, AT + HOUR - SEC):
        result = lookup(archive, alarm)
        assert result["durum"] == "yok" and result["baglam"] is None
        assert result["mesaj"] == "İlgili güncel gelişme bulunamadı."
    assert lookup(archive, AT + HOUR)["durum"] == "var"
    # Yayın eskiyse yeni bir gözlem süreyi uzatmaz. Alt sınır dahil.
    assert lookup(archive, AT + HOUR, max_age=HOUR)["durum"] == "var"
    assert lookup(archive, AT + HOUR + SEC, max_age=HOUR)["durum"] == "yok"


def test_expiry_never_revives_an_older_version(archive):
    save(archive, item())
    save(archive, item(), known=AT + HOUR, valid_until=AT + 2 * HOUR)
    assert lookup(archive, AT + 2 * HOUR - SEC)["durum"] == "var"
    assert lookup(archive, AT + 2 * HOUR)["durum"] == "yok"


@pytest.mark.parametrize("reverse", [False, True])
def test_detail_with_cancellation_supersedes_replayed_light_without_lookahead(archive, reverse):
    row = item()
    versions = [(AT, None), (AT + HOUR, SALE + " Ancak sözleşme feshedildi."), (AT + 2 * HOUR, None)]
    for ts, body in (reversed(versions) if reverse else versions):
        save(archive, row, known=ts, body=body, replay=reverse)
    assert lookup(archive, AT)["durum"] == "var"
    for ts in (AT + HOUR, AT + 2 * HOUR):
        assert lookup(archive, ts)["durum"] == "yok"
    # Başka bir olayı örtmez: as-of kalan son geçerli haber seçilir.
    save(archive, item(5, at=AT - HOUR))
    assert lookup(archive, AT + HOUR)["baglam"]["kaynak"] == "senaryo:5"


def test_exact_replay_idempotent_and_equal_strength_conflicts_are_held(archive):
    row = item()
    assert save(archive, row) == save(archive, row) == "hazir"
    revised = replace(row, disclosure=replace(row.disclosure, summary=SALE.replace("60", "90")))
    reason = archive.record(revised, origin="sentetik", topic="satis_sozlesmesi",
                            quote=revised.disclosure.summary, known_at=AT)
    assert reason == "surum_celiskisi" and lookup(archive)["baglam"] is None
    assert save(archive, row) == "surum_celiskisi"
    # Eşdeğer çelişkiyi sonradan doğrulanmış gövde çözebilir.
    save(archive, row, body=SALE, known=AT + HOUR)
    assert lookup(archive, AT + HOUR)["baglam"]["olay"] == SALE


def test_stock_scope_and_real_evidence_are_required(archive):
    row = item()
    market = replace(row, ticker=None, status="agenda", disclosure=replace(row.disclosure, tickers=()))
    assert save(archive, market) == "kapsam_disi"
    only_title = replace(row, disclosure=replace(row.disclosure, summary=None))
    assert archive.record(only_title, origin="sentetik", known_at=AT, topic="sirket_aciklamasi",
                          quote=only_title.disclosure.subject) != "hazir"
    assert lookup(archive)["baglam"] is None


def test_mismatched_detail_cannot_replace_good_record(archive):
    row = item()
    save(archive, row)
    with pytest.raises(ValueError, match="detay"):
        archive.record(row, origin="sentetik", known_at=AT + HOUR, topic="satis_sozlesmesi", quote=SALE,
                       detail=DisclosureDetail(replace(row.disclosure, index=77), SALE, 0))
    assert lookup(archive, AT + HOUR)["durum"] == "var"


@pytest.mark.parametrize("ticker,at", [("DENEM,THYAO", AT), ("NYSE:DENEM", AT), ("", AT),
                                      ("DENEM", AT.replace(tzinfo=None))])
def test_invalid_alarm_is_not_reported_as_no_news(archive, ticker, at):
    with pytest.raises(ValueError):
        lookup(archive, at, ticker)


def test_invalid_time_policy_rejected(archive):
    with pytest.raises(ValueError):
        save(archive, item(), known=AT - SEC)
    with pytest.raises(ValueError):
        lookup(archive, max_age=timedelta(0))


def test_query_reopens_persisted_archive_read_only(tmp_path):
    path = tmp_path / "yerel #1.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        with open_archive(path):
            pass
    assert not path.exists()
    with open_archive(path, writable=True) as store:
        save(store, item())
    with open_archive(path) as reader:
        assert lookup(reader)["durum"] == "var"
        with pytest.raises(sqlite3.OperationalError):
            save(reader, item(2))


def test_explicit_correction_retires_only_its_target_when_known(archive):
    old = item(10)
    save(archive, old)
    other_event = item(20, at=AT - HOUR)
    save(archive, other_event)
    text = "Önceki açıklamada bildirilen sözleşme taraflarca feshedilmiştir."
    revised = item(11, at=AT + HOUR, text=text)
    assert archive.record(revised, origin="sentetik", topic="sirket_aciklamasi", quote=text,
                          detail=DisclosureDetail(revised.disclosure, text, 0),
                          known_at=AT + HOUR + SEC, supersedes=10, valid_until=AT + 2 * HOUR) == "hazir"
    # Fesih yayımlanmış olsa da düzeltilmiş kanıt arşive henüz girmemişti.
    assert lookup(archive, AT + HOUR)["baglam"]["kaynak"] == "senaryo:10"
    assert lookup(archive, AT + HOUR + SEC)["baglam"]["olay"] == text
    # Düzeltme güncellikten çıkınca eski sözleşme geri gelmez, ışık kaydı tekrar gelse de.
    save(archive, old, known=AT + 3 * HOUR)
    assert lookup(archive, AT + 3 * HOUR)["baglam"]["kaynak"] == "senaryo:20"


@pytest.mark.parametrize("target", [99, 1, 2])
def test_bad_replacement_link_does_not_publish_or_retire(archive, target):
    save(archive, item(1, ticker="BASKA"))
    row = item(2, at=AT + HOUR)
    with pytest.raises(ValueError):
        save(archive, row, body=SALE, supersedes=target)
    assert lookup(archive, AT + HOUR, "BASKA")["durum"] == "var"
    assert lookup(archive, AT + HOUR)["durum"] == "yok"


def test_generic_title_confirmed_stoppage_does_not_require_numeric_score(archive):
    quote = "Döküm hattında bakım nedeniyle üretime ara verilmiştir."
    row = item(text=quote)
    generic = replace(row, disclosure=replace(row.disclosure, subject="Özel Durum Açıklaması (Genel)"))
    assert archive.record(generic, origin="sentetik", topic="uretim_durusu", quote=quote, known_at=AT) == "hazir"
    context = lookup(archive)["baglam"]
    assert context["olay"] == quote and "süresine" in context["onemi"]


@pytest.mark.parametrize("reverse", [False, True])
def test_exact_packet_replays_cannot_clear_a_conflict_or_an_expiry(archive, reverse):
    row = item()
    other = item(text=SALE.replace("60", "90"))
    versions = [(row, AT), (other, AT), (row, AT + HOUR)]
    for version, known in (reversed(versions) if reverse else versions):
        archive.record(version, origin="sentetik", known_at=known, topic="satis_sozlesmesi",
                       quote=version.disclosure.summary, replay=reverse)
    assert lookup(archive, AT + HOUR)["durum"] == "yok"
    expiring = item(2)
    save(archive, expiring)
    save(archive, expiring, known=AT + HOUR, valid_until=AT + 2 * HOUR)
    save(archive, expiring, known=AT + 3 * HOUR)
    assert lookup(archive, AT + 3 * HOUR)["durum"] == "yok"


def test_later_qualified_summary_cannot_leave_old_sale_detail_affirmative(archive):
    save(archive, item(), body=SALE)
    assert save(archive, item(text=SALE + " Ancak sözleşme feshedildi."), known=AT + SEC) != "hazir"
    assert lookup(archive)["durum"] == "var"
    assert lookup(archive, AT + SEC)["durum"] == "yok"


def test_revenue_comparison_names_its_separate_point_in_time_source(archive):
    table = FundamentalRow("DENEM", "2026-09-30", revenue_ttm=300_000_000, source="senaryo:mali-1")
    available = PublishedRow(table, AT - SEC)
    later = PublishedRow(replace(table, revenue_ttm=600_000_000, source="senaryo:mali-2"), AT + SEC)
    save(archive, item(), statements=[later, available])
    answer = lookup(archive, AT + HOUR)["baglam"]
    assert "%20" in answer["onemi"] and answer["kaynak"] == "senaryo:1"
    assert answer["mali_tablo"] == {"kaynak": table.source, "donem_sonu": table.period_end,
                                    "yayin_zamani": available.published_at.isoformat()}


def test_review_never_substitutes_for_source_evidence_or_appears_before_review(archive):
    text = "Bakım tamamlandı ve üretim yeniden başladı."
    row = item(text=text)
    note = ReviewedReading("Şirket üretime yeniden başladığını bildirdi.", "Açıklama duruşun sona erdiğini gösteriyor.")
    assert archive.record(row, origin="sentetik", known_at=AT, topic="sirket_aciklamasi",
                          quote="Kaynakta olmayan ifade.", reviewed=note) == "alinti_kayitta_yok"
    assert archive.record(row, origin="sentetik", known_at=AT + SEC, topic="sirket_aciklamasi",
                          quote=text, reviewed=note) == "hazir"
    assert lookup(archive, AT)["baglam"] is None
    assert lookup(archive, AT + SEC)["baglam"]["olay"] == note.event


def test_default_seven_calendar_days_and_stable_publication_tie(archive):
    save(archive, item(2))
    save(archive, item(1))
    assert lookup(archive, AT + timedelta(days=7))["baglam"]["kaynak"] == "senaryo:2"
    assert lookup(archive, AT + timedelta(days=7, microseconds=1))["durum"] == "yok"


def test_detail_must_actually_support_the_quote_to_override_a_known_withdrawal(archive):
    row = item()
    save(archive, row, body=SALE)
    save(archive, row, body=SALE + " Ancak sözleşme feshedildi.", known=AT + SEC)
    # Detay nesnesi var, fakat eski olumlu alıntı yalnız liste özetinde.
    save(archive, row, body="Toplantı tutanağı ektedir.", known=AT + HOUR)
    assert lookup(archive, AT + HOUR)["durum"] == "yok"


def test_live_known_at_cannot_be_backdated_by_reprocessing(archive):
    published = datetime(2026, 11, 17, 11, 53, 7, tzinfo=timezone.utc)
    ready = datetime(2026, 11, 17, 12, 2, 0, tzinfo=timezone.utc)
    alarm_15_00 = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
    row = item(90, at=published)
    assert save(archive, row, body=SALE, known=ready) == "hazir"
    assert lookup(archive, alarm_15_00)["durum"] == "yok"
    assert lookup(archive, ready)["durum"] == "var"
    # Yanlışlıkla yayın saati verilerek hem canlı hem replay ile tekrar işlense de 15:00 değişmez.
    assert save(archive, row, body=SALE, known=published) == "hazir"
    assert save(archive, row, body=SALE, known=published, replay=True) == "hazir"
    assert lookup(archive, alarm_15_00)["durum"] == "yok"
    assert lookup(archive, ready)["durum"] == "var"


def test_multi_ticker_update_retracts_prior_single_ticker_without_lookahead(archive):
    prior = item(200, at=AT - HOUR)
    save(archive, prior, body=SALE)
    single = item(201, at=AT)
    single_light = replace(single, disclosure=replace(single.disclosure, tickers=()))
    assert save(archive, single_light, known=AT) == "hazir"
    assert lookup(archive, AT)["baglam"]["kaynak"] == "senaryo:201"

    # Yabancı bir bildirimin detayı geri çekme gerekçesi olamaz.
    multi = ProcessedDisclosure(
        replace(single.disclosure, tickers=("DENEM", "BASKA")),
        Route.PIPELINE, "unresolved_ticker", ticker=None,
    )
    with pytest.raises(ValueError, match="detay"):
        archive.record(
            multi, origin="sentetik", known_at=AT + SEC, topic="satis_sozlesmesi",
            quote=SALE, detail=DisclosureDetail(replace(multi.disclosure, index=999), SALE, 0),
        )
    assert lookup(archive, AT + SEC)["baglam"]["kaynak"] == "senaryo:201"

    # Gerçek çok hisseli detay ulaştığı andan itibaren yanlış tek-hisse atfı düşer.
    assert archive.record(
        multi, origin="sentetik", known_at=AT + HOUR, topic="satis_sozlesmesi",
        quote=SALE, detail=DisclosureDetail(multi.disclosure, SALE, 0),
    ) == "geri_cekildi"
    assert lookup(archive, AT + HOUR - SEC)["baglam"]["kaynak"] == "senaryo:201"
    assert lookup(archive, AT + HOUR)["baglam"]["kaynak"] == "senaryo:200"
    # Eski tek hisseli liste satırının tekrar gelmesi geri çekilen atfı diriltmez.
    save(archive, single_light, known=AT + 2 * HOUR)
    assert lookup(archive, AT + 2 * HOUR)["baglam"]["kaynak"] == "senaryo:200"


def test_editorial_mistake_on_unchanged_source_preserves_verified_answer(archive):
    row = item(301, text="Yeni Sözleşme")
    assert save(archive, row, body=SALE, known=AT) == "hazir"
    # Aynı gövdeye bozuk alıntı verildiğinde hata döner ama doğru kayıt silinmez.
    assert archive.record(
        row, origin="sentetik", known_at=AT + SEC, topic="satis_sozlesmesi",
        quote="Gövdede olmayan cümle.", detail=DisclosureDetail(row.disclosure, SALE, 0),
    ) == "alinti_kayitta_yok"
    assert lookup(archive, AT + HOUR)["durum"] == "var"

    # Gövde gerçekten değişip satış ifadesi kaldırılırsa eski cevap artık verilmez.
    assert archive.record(
        row, origin="sentetik", known_at=AT + 2 * HOUR, topic="satis_sozlesmesi",
        quote=SALE, detail=DisclosureDetail(row.disclosure, "Açıklama geri çekilmiştir.", 0),
    ) == "alinti_kayitta_yok"
    assert lookup(archive, AT + HOUR)["durum"] == "var"
    assert lookup(archive, AT + 2 * HOUR)["durum"] == "yok"


def test_negated_or_resumed_stoppage_and_polarity_inversion_are_rejected(archive):
    neg = "Tesislerimizde herhangi bir üretim duruşu yaşanmamıştır."
    assert archive.record(
        item(401, text=neg), origin="sentetik", known_at=AT, topic="uretim_durusu", quote=neg,
    ) == "durus_kaniti_yetersiz"
    assert lookup(archive, AT)["durum"] == "yok"

    stop = "Döküm hattında arıza nedeniyle üretime ara verilmiştir."
    assert archive.record(
        item(402, ticker="HATAS", text=stop), origin="sentetik", known_at=AT,
        topic="uretim_durusu", quote=stop,
    ) == "hazir"
    assert lookup(archive, AT, "HATAS")["durum"] == "var"
    resumed = stop + " Arıza giderilmiş olup üretim yeniden başlamıştır."
    assert archive.record(
        item(402, ticker="HATAS", text=resumed), origin="sentetik", known_at=AT + HOUR,
        topic="uretim_durusu", quote=stop,
    ) == "durus_kaniti_yetersiz"
    assert lookup(archive, AT + HOUR - SEC, "HATAS")["durum"] == "var"
    assert lookup(archive, AT + HOUR, "HATAS")["durum"] == "yok"

    denial = "Şirketimiz hakkında açıklanmış bir idari para cezası bulunmamaktadır."
    bad_note = ReviewedReading("Şirkete idari para cezası uygulandı.", "Ceza nakit akışını etkiler.")
    assert archive.record(
        item(403, ticker="TEMIZ", text=denial), origin="sentetik", known_at=AT,
        topic="sirket_aciklamasi", quote=denial, reviewed=bad_note,
    ) == "okuma_anlam_celiskisi"
    assert lookup(archive, AT, "TEMIZ")["durum"] == "yok"


def test_ticker_corrected_record_can_be_superseded_and_out_of_order_resolves(archive):
    # 1) Önce yanlış hisseye yazılıp sonra doğru hisseye düzeltilen kayıt sonradan feshedilebilir.
    save(archive, item(501, ticker="YANLS"), body=SALE, known=AT)
    save(archive, item(501, ticker="DOGRU"), body=SALE, known=AT + SEC)
    cancel = "Önceki açıklamada bildirilen sözleşme taraflarca feshedilmiştir."
    rep = item(502, ticker="DOGRU", at=AT + HOUR, text=cancel)
    assert archive.record(
        rep, origin="sentetik", topic="sirket_aciklamasi", quote=cancel,
        detail=DisclosureDetail(rep.disclosure, cancel, 0), known_at=AT + HOUR, supersedes=501,
    ) == "hazir"
    assert lookup(archive, AT + SEC, "DOGRU")["baglam"]["kaynak"] == "senaryo:501"
    assert lookup(archive, AT + HOUR, "DOGRU")["baglam"]["kaynak"] == "senaryo:502"

    # 2) Düzeltme (#601) hedefinden (#600) önce gelirse bekler; hedef geldiğinde otomatik bağlanır.
    early_fix = item(601, ticker="SIRAS", at=AT + HOUR, text=cancel)
    assert archive.record(
        early_fix, origin="sentetik", topic="sirket_aciklamasi", quote=cancel,
        detail=DisclosureDetail(early_fix.disclosure, cancel, 0),
        known_at=AT + HOUR, supersedes=600, valid_until=AT + 2 * HOUR, defer_missing_target=True,
    ) == "hedef_bekleniyor"
    assert lookup(archive, AT + HOUR, "SIRAS")["durum"] == "yok"
    target = item(600, ticker="SIRAS", at=AT)
    assert save(archive, target, body=SALE, known=AT + HOUR + SEC) == "hazir"
    assert lookup(archive, AT + HOUR + SEC, "SIRAS")["baglam"]["kaynak"] == "senaryo:601"
    # Düzeltmenin süresi dolduğunda eski satış geri dönmez.
    assert lookup(archive, AT + 3 * HOUR, "SIRAS")["durum"] == "yok"

    # 3) Bekleyen düzeltmenin hedefi başka hisseye ait çıkarsa hedef korunur, yanlış bağ düşer.
    wrong_fix = item(701, ticker="SIRAS", at=AT + HOUR, text=cancel)
    assert archive.record(
        wrong_fix, origin="sentetik", topic="sirket_aciklamasi", quote=cancel,
        detail=DisclosureDetail(wrong_fix.disclosure, cancel, 0),
        known_at=AT + HOUR, supersedes=700, defer_missing_target=True,
    ) == "hedef_bekleniyor"
    assert save(archive, item(700, ticker="OBURU", at=AT), body=SALE, known=AT + HOUR + SEC) == "hazir"
    assert lookup(archive, AT + 2 * HOUR, "OBURU")["baglam"]["kaynak"] == "senaryo:700"
    assert lookup(archive, AT + 2 * HOUR, "SIRAS")["durum"] == "yok"


def test_scan_watermark_and_pending_detail_distinguish_empty_answers(archive):
    empty = lookup(archive, AT)
    assert empty["durum"] == "yok" and empty["neden"] == "tarama_kaydi_yok"
    assert "son_tarama" not in empty

    with pytest.raises(ValueError):
        archive.record_scan(AT + SEC, observed_at=AT, origin="sentetik")

    archive.record_scan(AT + 30 * SEC, observed_at=AT + 30 * SEC, origin="sentetik")
    covered = lookup(archive, AT + 30 * SEC)
    assert covered["neden"] == "uygun_gelisme_yok"
    assert covered["son_tarama"] == (AT + 30 * SEC).isoformat()

    stale = lookup(archive, AT + HOUR)
    assert stale["neden"] == "tarama_geride"
    assert stale["son_tarama"] == (AT + 30 * SEC).isoformat()

    tender = DisclosureStub(800, AT + 10 * SEC, "Fonet A.Ş.", "İhale Süreci / Sonucu",
                            summary="İhale süreci başlatıldı.", tickers=("FONET",))
    archive.record_pending(tender, ticker="FONET", known_at=AT + 20 * SEC,
                           reason="ihale_sonucu_icin_detay_gerekli", origin="sentetik")
    pending = lookup(archive, AT + 30 * SEC, "FONET")
    assert pending["durum"] == "yok" and pending["neden"] == "detay_bekleniyor"
    assert pending["son_tarama"] == (AT + 30 * SEC).isoformat()


def test_wal_concurrent_read_during_write_and_cross_thread_helper(tmp_path):
    import threading
    from bistman.alarm_context import kap_baglami_ekle

    db_path = tmp_path / "canli.sqlite"
    with open_archive(db_path, writable=True) as writer:
        writer.record(item(1, ticker="MTRKS"), origin="kap_arsiv", known_at=AT,
                      topic="satis_sozlesmesi", quote=SALE)
        # Açık yazma işlemi sürerken grafik alarmı salt okunur sorgu yapabilmeli (WAL).
        writer._db.execute("BEGIN IMMEDIATE")
        writer._db.execute(
            "INSERT INTO alarm_scans(origin, observed, covered) VALUES (?, ?, ?)",
            ("kap_arsiv", (AT + SEC).isoformat(timespec="microseconds"),
             (AT + SEC).isoformat(timespec="microseconds")),
        )
        try:
            req: dict[str, object] = {"hisse": "MTRKS"}
            res = kap_baglami_ekle(req, db_path, "MTRKS", AT)
            assert res is not None and res["durum"] == "var"
            assert req["kap_baglami"]["olay"] == SALE
        finally:
            writer._db.commit()

    # Aynı salt okunur nesne `asyncio.to_thread` gibi farklı bir thread'de çalışabilmeli.
    with open_archive(db_path) as reader:
        out: list[dict] = []
        t = threading.Thread(target=lambda: out.append(reader.alarm_baglami("MTRKS", AT)))
        t.start()
        t.join()
        assert out[0]["durum"] == "var"

    # Olmayan DB 'haber yok' uydurmaz; None döner ve isteği bozmaz.
    untouched: dict[str, object] = {"grafik": "kırılım"}
    assert kap_baglami_ekle(untouched, tmp_path / "eksik.sqlite", "MTRKS", AT) is None
    assert untouched == {"grafik": "kırılım"}


def test_history_minute_stub_matches_real_mtrks_detail_and_keeps_exact_second(archive):
    from datetime import date
    from pathlib import Path
    from bistman.backfill import stub_from_row
    from bistman.context_packet import make_candidate
    from bistman.parsing.kap_table import body_text, parse_history
    from bistman.sources.kap import parse_detail

    fix = Path(__file__).parent / "fixtures" / "kap"
    rows = parse_history((fix / "member_history_mtrks.html").read_bytes(), today=date(2026, 11, 17))
    detail = parse_detail((fix / "detail_1699030_live.json").read_bytes())
    hist_stub = stub_from_row(rows[0], "MTRKS")
    assert hist_stub.minute_precision is True
    assert detail.matches(hist_stub, "MTRKS") is True

    text = body_text(detail.body_html)
    quote = "Bu ortaklar arasında iddialarda adı geçen kişi ve kurumlar yoktur."
    assert quote in text
    assert archive.record(
        make_candidate(hist_stub, "MTRKS"), origin="kap_arsiv",
        known_at=detail.stub.published_at + SEC, topic="sirket_aciklamasi",
        quote=quote, detail=detail,
    ) == "hazir"
    ans = archive.alarm_baglami("MTRKS", detail.stub.published_at + SEC)
    assert ans["durum"] == "var"
    assert ans["baglam"]["yayin_zamani"] == "2026-11-17T11:53:07+00:00"


def test_legacy_archive_schema_works_readonly_and_migrates_on_write(tmp_path):
    legacy = tmp_path / "eski.sqlite"
    con = sqlite3.connect(legacy)
    con.executescript("""
        CREATE TABLE alarm_versions (
            origin TEXT NOT NULL, disclosure_id INTEGER NOT NULL,
            version BLOB NOT NULL, ticker TEXT NOT NULL,
            published TEXT NOT NULL, known TEXT NOT NULL,
            strength INTEGER NOT NULL, valid_until TEXT,
            held TEXT, answer TEXT,
            PRIMARY KEY (origin, disclosure_id, version)
        ) STRICT;
        CREATE TABLE alarm_replacements (
            origin TEXT NOT NULL, target INTEGER NOT NULL,
            replacement INTEGER NOT NULL, known TEXT NOT NULL,
            PRIMARY KEY (origin, target, replacement)
        ) STRICT;
    """)
    reading = json.dumps({"hisse": "MTRKS", "konu": "sirket_aciklamasi", "olay": SALE,
                          "onemi": "test", "kaynak": "https://www.kap.org.tr/tr/Bildirim/1",
                          "yayin_zamani": AT.isoformat()})
    con.execute(
        "INSERT INTO alarm_versions VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
        ("kap_arsiv", 1, b"\x01" * 32, "MTRKS",
         AT.isoformat(timespec="microseconds"), AT.isoformat(timespec="microseconds"), 1, reading),
    )
    con.commit()
    con.close()

    # Eski şema salt okunur açıldığında bile bozulmadan çalışmalı.
    with open_archive(legacy) as reader:
        assert reader.alarm_baglami("MTRKS", AT)["durum"] == "var"
        empty = reader.alarm_baglami("THYAO", AT)
        assert empty["durum"] == "yok" and empty["neden"] == "tarama_kaydi_yok"

    # Yazılabilir açıldığında eksik sütun ve tablolar kayıpsız eklenmeli.
    with open_archive(legacy, writable=True) as writer:
        writer.record_scan(AT + SEC, observed_at=AT + SEC, origin="kap_arsiv")
        assert writer.alarm_baglami("MTRKS", AT + SEC)["durum"] == "var"
        assert writer.alarm_baglami("THYAO", AT + SEC)["neden"] == "uygun_gelisme_yok"


def test_minute_retract_same_time_pending_newer_pending_notice_and_maintenance(tmp_path):
    from bistman.alarm_context import kap_baglami_ekle
    from bistman.context_packet import make_candidate
    from bistman.sources.kap_companies import CompanyEntry

    db_path = tmp_path / "bakim.sqlite"
    with open_archive(db_path, writable=True) as archive:
        # 1) Dakika hassasiyetli kayıt aynı dakikanın tam saniyeli detayıyla geri çekilebilir;
        #    tam saniyeli kayıt ise farklı saniyeli geri çekmeyi reddeder.
        min_stub = DisclosureStub(901, AT, "Test A.Ş.", "Yeni İş", summary=SALE,
                                  tickers=("DAKIK",), minute_precision=True)
        archive.record(make_candidate(min_stub, "DAKIK"), origin="kap_arsiv",
                       known_at=AT + HOUR, topic="satis_sozlesmesi", quote=SALE)
        assert archive.retract(901, published_at=AT + 42 * SEC,
                               known_at=AT + 2 * HOUR, reason="cok_hisseli") == "geri_cekildi"
        assert archive.alarm_baglami("DAKIK", AT + HOUR)["durum"] == "var"
        assert archive.alarm_baglami("DAKIK", AT + 2 * HOUR)["durum"] == "yok"

        exact_stub = DisclosureStub(902, AT, "Test A.Ş.", "Yeni İş", summary=SALE, tickers=("KESIN",))
        archive.record(make_candidate(exact_stub, "KESIN"), origin="kap_arsiv",
                       known_at=AT + HOUR, topic="satis_sozlesmesi", quote=SALE)
        with pytest.raises(ValueError, match="uyuşmadı"):
            archive.retract(902, published_at=AT + 42 * SEC, known_at=AT + 2 * HOUR)

        # 2) Aynı `known_at` anında yazılan bekleme satırı doğrulanmış özetle çelişki üretmez.
        same_stub = DisclosureStub(903, AT, "Test A.Ş.", "Yeni İş", summary=SALE, tickers=("AYNIA",))
        archive.record_pending(same_stub, ticker="AYNIA", known_at=AT + SEC, reason="detay_bekleniyor")
        assert archive.record(make_candidate(same_stub, "AYNIA"), origin="kap_arsiv",
                              known_at=AT + SEC, topic="satis_sozlesmesi", quote=SALE) == "hazir"
        ans_same = archive.alarm_baglami("AYNIA", AT + SEC)
        assert ans_same["durum"] == "var" and "bekleyen_bildirim" not in ans_same

        # 3) Daha yeni ama henüz bekleyen bir bildirim varsa eski `hazir` yanıta not eklenir;
        #    yeni bildirim doğrulandığında ise o seçilir ve bekleme notu kalkar.
        newer_stub = DisclosureStub(904, AT + HOUR, "Test A.Ş.", "Özel Durum Açıklaması (Genel)",
                                    tickers=("AYNIA",))
        archive.record_pending(newer_stub, ticker="AYNIA", known_at=AT + HOUR + SEC,
                               reason="inceleme_bekleniyor")
        req: dict[str, object] = {}
        with_pending = kap_baglami_ekle(req, db_path, "AYNIA", AT + HOUR + SEC)
        assert with_pending is not None and with_pending["durum"] == "var"
        assert with_pending["baglam"]["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/903"
        assert with_pending["bekleyen_bildirim"]["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/904"
        assert req["kap_bekleyen_bildirim"]["neden"] == "detay_bekleniyor"

        newer_sale = "Şirketimiz ile XYZ A.Ş. arasında 95 milyon TL bedelli satış sözleşmesi imzalanmıştır."
        archive.record(
            make_candidate(newer_stub, "AYNIA"), origin="kap_arsiv",
            known_at=AT + 2 * HOUR, topic="satis_sozlesmesi", quote=newer_sale,
            detail=DisclosureDetail(newer_stub, newer_sale, 0),
        )
        resolved = archive.alarm_baglami("AYNIA", AT + 2 * HOUR)
        assert resolved["baglam"]["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/904"
        assert "bekleyen_bildirim" not in resolved

        # 4) Şirket önbelleği (TTL + bayat yedek) ve güvenli budama (son tarama korunur).
        saved = archive.save_companies([CompanyEntry("MTRKS", "oid1", "MATRİKS A.Ş.")], updated_at=AT)
        assert saved == 1
        assert len(archive.load_companies(as_of=AT + 12 * HOUR, max_age=timedelta(hours=24))) == 1
        assert archive.load_companies(as_of=AT + 25 * HOUR, max_age=timedelta(hours=24)) == []
        assert len(archive.load_companies(as_of=AT + 25 * HOUR, max_age=None)) == 1

        archive.record_scan(AT, observed_at=AT)
        archive.record_scan(AT + HOUR, observed_at=AT + HOUR)
        archive.mark_seen(1, b"x" * 32, "filtered_routine", updated_at=AT)
        pruned = archive.prune(AT + timedelta(days=40), seen_days=14, version_days=30, origin="kap_arsiv")
        assert pruned["seen"] == 1 and pruned["scans"] == 1 and pruned["versions"] >= 1
        # En son tarama damgası budamada asla silinmez.
        assert archive.last_scan(origin="kap_arsiv") == AT + HOUR
