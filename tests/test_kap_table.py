"""`parse_history` + `body_text` — GERÇEK KAP yanıtlarına karşı.

Fixture'lar 17.11.2026'da canlı KAP'tan indirildi:
  member_history_mtrks.html  1 yıllık geçmiş tablosu (89 bildirim)
  detail_1699030_live.json   bir bildirimin gövdesi (GWT HTML)

Bu testler tablonun **şeklini** kilitler: KAP sütun ekleyip çıkarırsa
`KapTableError` ile patlarız, sessizce eksik geçmiş yazmayız.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from bistman.parsing.kap_table import (
    HistoryRow,
    KapTableError,
    body_text,
    html_to_text,
    parse_history,
    parse_related,
    parse_when,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures" / "kap"
HISTORY_HTML = (FIXTURES / "member_history_mtrks.html").read_text(encoding="utf-8")
DETAIL = json.loads((FIXTURES / "detail_1699030_live.json").read_text(encoding="utf-8"))[0]
BODY_HTML = DETAIL["disclosureBody"][0]
TODAY = dt.date(2026, 11, 17)


@pytest.fixture(scope="module")
def rows() -> list[HistoryRow]:
    return parse_history(HISTORY_HTML, today=TODAY)


# ----------------------------------------------------------------- gerçek tablo


def test_all_rows_parsed(rows: list[HistoryRow]) -> None:
    """Canlı sayfa '89 bildirim bulundu' dedi; parser da 89 satır vermeli."""
    assert len(rows) == 89


def test_class_distribution_matches_page(rows: list[HistoryRow]) -> None:
    """Sınıf dağılımı canlı sayfayla birebir aynı: 39 DKB, 27 ÖDA, 13 FR, 10 DG."""
    assert summarize(rows) == {"dkb": 39, "öda": 27, "fr": 13, "dg": 10}


def test_dg_class_is_administrative_forms(rows: list[HistoryRow]) -> None:
    """DG = idari form (genel bilgi formu, yatırımcı raporu): 10 satır, hepsi bilgi taşımaz."""
    dg = [r for r in rows if r.disclosure_class == "dg"]
    assert len(dg) == 10
    assert all(r.tickers == ("MTRKS",) for r in dg)


def test_newest_row_fields(rows: list[HistoryRow]) -> None:
    first = rows[0]
    assert first.index == 1699030
    assert first.published_at == dt.datetime(2026, 11, 17, 14, 53)
    assert first.disclosure_class == "öda"
    assert first.tickers == ("MTRKS",)
    assert first.related_total is None


def test_company_title_subject_concatenation_is_split(rows: list[HistoryRow]) -> None:
    """Tabloda unvan ve konu tek hücrede; parser konuyu kesip unvanı bırakmalı."""
    assert rows[0].company_title == "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."
    assert rows[0].subject == "Haber ve Söylentilere İlişkin Açıklama"


def test_dkb_rows_have_no_issuer_code(rows: list[HistoryRow]) -> None:
    """DKB'de 'Kod' sütunu daima boş — ihraççı BISTECH, hisse ilgili-sütunda."""
    dkb = [r for r in rows if r.disclosure_class == "dkb"]
    assert len(dkb) == 39
    assert all(r.tickers == () for r in dkb)


def test_dkb_related_symbols(rows: list[HistoryRow]) -> None:
    """İlgili hisse sütunu gerçek: 29 satır MTRKS, 4 satır çok sembollü, 6 satır BOŞ."""
    dkb = [r for r in rows if r.disclosure_class == "dkb"]
    assert sum(1 for r in dkb if r.related == ("MTRKS",)) == 29
    assert sum(1 for r in dkb if len(r.related) > 1) == 4
    assert sum(1 for r in dkb if not r.related) == 6


def test_rows_without_any_attribution_exist(rows: list[HistoryRow]) -> None:
    """6 DKB satırında ne kod ne ilgili hisse var: atıf üye haritasından yapılmalı.

    Bunlar piyasa geneli duyurular (12-(4) KAP duyurusu, SPK işlem yasağı,
    dönüşüm duyurusu). Tablodan hisse çıkmıyor -> `member_oid` çözer.
    """
    orphans = [r for r in rows if not r.tickers and not r.related]
    assert len(orphans) == 6
    subjects = {r.subject for r in orphans}
    assert "SPK İşlem Yasağı Nedeniyle Pay Duyurusu" in subjects
    assert any("12-(4)" in r.summary for r in orphans)


def test_multi_symbol_rows_are_market_wide(rows: list[HistoryRow]) -> None:
    """Çok sembollü satırlar piyasa geneli (BERA, KRGYO): çift atıf riski var."""
    multi = [r for r in rows if len(r.related) > 1]
    assert {r.related for r in multi} == {("BERA", "MTRKS"), ("KRGYO", "MTRKS")}


def test_period_only_on_financial_reports(rows: list[HistoryRow]) -> None:
    fr = [r for r in rows if r.disclosure_class == "fr"]
    assert fr, "finansal rapor satırı yok"
    assert any(r.period for r in fr), "dönem alanı hiç dolmamış"
    assert all(r.period is None for r in rows if r.disclosure_class == "dkb")


def test_rows_sorted_newest_first(rows: list[HistoryRow]) -> None:
    stamps = [r.published_at for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_one_year_span(rows: list[HistoryRow]) -> None:
    assert (TODAY - rows[-1].published_at.date()).days >= 360


# --------------------------------------------------------------- küçük parçalar


def test_parse_related_simple() -> None:
    assert parse_related("<div><div>MTRKS</div></div>") == (("MTRKS",), None)


def test_parse_related_dash_is_empty() -> None:
    assert parse_related("<div>-</div>") == ((), None)


def test_parse_related_truncated_list() -> None:
    """`(+N)` = ilgili hisse listesi KESİK; eksik atıf sessizce kabul edilmemeli."""
    tickers, total = parse_related("<div><div>IDH</div><br><div>IEV</div><div>(+6)</div></div>")
    assert tickers == ("IDH", "IEV")
    assert total == 8  # 2 gösterilen + 6 gizli


def test_truncated_flag_on_row() -> None:
    row = HistoryRow(
        index=1,
        published_at=dt.datetime(2026, 1, 1),
        disclosure_class="öda",
        company_title="X A.Ş.",
        subject="S",
        summary="",
        tickers=("IDH",),
        related=("IDH", "IEV"),
        related_total=8,
    )
    assert row.related_truncated is True


def test_parse_when_absolute() -> None:
    assert parse_when("05.11.2026 19:36") == dt.datetime(2026, 11, 5, 19, 36)


def test_parse_when_relative_bugun() -> None:
    """Sayfa 'Bugün 14:53' yazıyor; mutlak tarihe çevrilmeli (saat dilimi kayması yok)."""
    assert parse_when("Bugün 14:53", today=TODAY) == dt.datetime(2026, 11, 17, 14, 53)


def test_parse_when_relative_dun_rolls_over_month() -> None:
    assert parse_when("Dün 09:12", today=dt.date(2026, 3, 1)) == dt.datetime(2026, 2, 28, 9, 12)


def test_relative_date_via_parse_history(rows: list[HistoryRow]) -> None:
    """Fixture'ın en üst satırı 'Bugün' biçiminde geliyor."""
    raw_row = HISTORY_HTML.split('id="notification1"', 1)[1][:3000]
    assert "Bugün" in raw_row


def test_html_to_text_keeps_line_breaks() -> None:
    parts = [p for p in html_to_text("<div>a</div><br>b &amp; c").split("\n") if p]
    assert parts == ["a", "b & c"]


# --------------------------------------------------------------------- gövde


def test_body_text_is_turkish_only() -> None:
    """Gövdede her metin TR + EN iki kez var; İngilizce kopya atılmalı."""
    text = body_text(BODY_HTML)
    assert "Haber veya Söylentilere İlişkin Açıklama" in text
    assert "Notification About News Or Rumours" not in text
    assert "Recently, various allegations" not in text


def test_body_text_drops_gwt_field_markers_and_placeholders() -> None:
    text = body_text(BODY_HTML)
    assert "oda_ExplanationTextBlock|" not in text
    assert "[CONSOLIDATION_METHOD]" not in text


def test_body_text_does_not_duplicate_amounts() -> None:
    """Aynı sayı metinde tek kez geçmeli — aksi halde tutar seçimi şaşar."""
    text = body_text(BODY_HTML)
    assert text.count("57.945") == 2  # metnin kendisinde iki farklı cümlede geçiyor
    assert text.count("Recently") == 0


def test_body_text_shrinks_payload() -> None:
    """TR-only filtre gövdeyi kabaca yarıya indirir (token/CPU tasarrufu)."""
    assert len(body_text(BODY_HTML)) < len(BODY_HTML) * 0.35


# ------------------------------------------------------------ yapı bozulunca


def test_non_html_raises() -> None:
    with pytest.raises(KapTableError, match="tablosu bulunamadı"):
        parse_history("<html><body>Giriş yapın</body></html>")


def test_table_without_rows_raises() -> None:
    with pytest.raises(KapTableError, match="hiç bildirim satırı"):
        parse_history('<table class="x"><tr><th>#</th></tr></table>')


def test_row_with_missing_columns_raises_in_strict_mode() -> None:
    """Sütun sayısı düşerse sessizce geçmemeli: backfill'de eksik geçmiş = yanlış kalibrasyon."""
    broken = (
        '<table><tr><td><input id="1699030" type="checkbox"/></td>'
        "<td>1</td><td>17.11.2026 14:53</td><td>MTRKS</td><td>X A.Ş.</td>"
        "<td>ÖDA</td><td>Konu</td><td>Özet</td></tr></table>"
    )
    with pytest.raises(KapTableError, match="12 sütun"):
        parse_history(broken)


def test_non_strict_mode_skips_broken_rows() -> None:
    broken = (
        '<table><tr><td><input id="1699030" type="checkbox"/></td>'
        "<td>1</td><td>17.11.2026 14:53</td><td>MTRKS</td><td>X A.Ş.</td>"
        "<td>ÖDA</td><td>Konu</td><td>Özet</td></tr></table>"
    )
    assert parse_history(broken, strict=False) == []
