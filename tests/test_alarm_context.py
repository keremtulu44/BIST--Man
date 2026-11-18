"""Dış alarm → kayıtlı kanıt. Ne skor ne de haberin gelişi tetikleyicidir."""

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
        save(archive, row, known=ts, body=body)
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
                       quote=version.disclosure.summary)
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
