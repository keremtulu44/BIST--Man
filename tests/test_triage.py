"""Triyaj testleri — konular 2026-11-17'de KAP'tan ALINAN GERÇEK kayıtlar."""

import pytest

from bistman.triage import Route, normalize_class, route, should_push


@pytest.mark.parametrize(
    ("cls", "subject", "expected"),
    [
        # ÖDA: asıl sinyal
        ("ODA", "Haber ve Söylentilere İlişkin Açıklama", Route.PIPELINE),
        ("ÖDA", "Pay Alım Satım Bildirimi", Route.PIPELINE),
        ("ODA", "Yeni Sözleşme İmzalanması", Route.PIPELINE),
        ("ODA", "Özel Durum Açıklaması (Genel)", Route.PIPELINE),
        # ÖDA ama rutin
        ("ODA", "Şirket Genel Bilgi Formu", Route.ROUTINE),
        ("ODA", "Pay Dökümü", Route.ROUTINE),
        ("ODA", "Genel Kurul İşlemlerine İlişkin Bildirim", Route.ROUTINE),
        ("ODA", "Pay Dışında Sermaye Piyasası Aracı İşlemlerine İlişkin Bildirim (Faiz İçeren)",
         Route.ROUTINE),
        # FR: push değil, veri tazeleme
        ("FR", "Finansal Rapor", Route.FUNDAMENTALS_REFRESH),
        ("FR", "Faaliyet Raporu (Konsolide)", Route.FUNDAMENTALS_REFRESH),
        ("FR", "Sorumluluk Beyanı (Konsolide)", Route.FUNDAMENTALS_REFRESH),
        # DKB: mekanik
        ("DKB", "Pay Bazında Devre Kesici Bildirimi", Route.MECHANICAL),
        ("DKB", "Borçlanma Araçları, Yatırım Fonları ve Varant İtfa/Kupon/Getiri/ Nakdi Uzlaşı "
                "Ödeme İşlemleri", Route.MECHANICAL),
        # DKB ama önemli istisna (SPK II-15.1 md.12-4)
        ("DKB", "Kamuyu Aydınlatma Platformu Duyurusu", Route.PIPELINE),
        # DKB, tanınmayan konu -> rutin
        ("DKB", "Şirket Unvan Değişikliği", Route.ROUTINE),
        # sınıf YOK (light akışı böyle) — konudan yakalanabilenler yakalanır
        (None, "Pay Bazında Devre Kesici Bildirimi", Route.MECHANICAL),
        (None, "Portföy Dağılım Raporu", Route.ROUTINE),
        (None, "Kamuyu Aydınlatma Platformu Duyurusu", Route.PIPELINE),
        # sınıf yok ve konu tanınmıyor -> fail-open
        (None, "Bilinmeyen Bir Bildirim", Route.PIPELINE),
        ("XYZ", "Bilinmeyen Sınıf", Route.PIPELINE),
    ],
)
def test_route(cls, subject, expected) -> None:
    assert route(cls, subject) is expected


def test_mechanical_detected_from_summary_when_subject_generic() -> None:
    """BISTECH kayıtlarında konu genel, ayrıntı özette olabiliyor."""
    r = route("DKB", "Diğer Bildirimler", "MTRKS.E işlem sırasında Pay Bazında Devre Kesici uygulaması")
    assert r is Route.MECHANICAL


def test_only_pipeline_is_pushable() -> None:
    assert should_push(Route.PIPELINE) is True
    for r in (Route.ROUTINE, Route.MECHANICAL, Route.FUNDAMENTALS_REFRESH):
        assert should_push(r) is False


def test_turkish_suffix_tolerance() -> None:
    """'Genel Kurul' ve 'Genel Kurul İşlemlerine İlişkin' ikisi de rutin olmalı."""
    assert route("ODA", "Genel Kurul") is Route.ROUTINE
    assert route("oda", "genel kurul işlemlerine ilişkin bildirim") is Route.ROUTINE


# --------------------------------------------------------------- DG (idari form)


@pytest.mark.parametrize(
    "subject",
    ["Şirket Genel Bilgi Formu", "Yatırımcı Raporu", "Katılım Finansı İlkeleri Bilgi Formu"],
)
def test_dg_class_is_routine(subject: str) -> None:
    """Gerçek geçmişte DG = idari form; skorlanmaz (kaydedilir)."""
    assert normalize_class("DG") == "dg"
    assert route("DG", subject) is Route.ROUTINE


@pytest.mark.parametrize("subject", ["Sermaye Artırımı", "Kâr Dağıtımı", "Pay Alım Satım Bildirimi"])
def test_dg_class_escalates_when_price_relevant(subject: str) -> None:
    """DG olmasına rağmen fiyat taşıyan konu hatta alınır (fail-open korunuyor)."""
    assert route("DG", subject) is Route.PIPELINE


def test_spk_trading_ban_is_not_mechanical() -> None:
    """Gerçek veride görüldü: SPK işlem yasağı DKB sınıfında ama piyasa olayı."""
    assert route("DKB", "SPK İşlem Yasağı Nedeniyle Pay Duyurusu") is Route.PIPELINE
    assert route("DKB", "Kamuyu Aydınlatma Platformu Duyurusu") is Route.PIPELINE
