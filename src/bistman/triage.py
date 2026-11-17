"""Bildirim triyajı — LLM'den ve gövde indirmeden ÖNCEki en ucuz filtre.

Neden var: 17.11.2026'da KAP akışından alınan gerçek örnekte MTRKS'in 1 yıllık
89 bildiriminin ~13'ü "devre kesici", ~6'sı kupon/ÖPY, ~1'i çok şirketli KAP
duyurusuydu. Gövde indirip skorlamak yerine **sınıf + konu** ile %70-80'i elenir.

Sınıf kodları (KAP):
    ÖDA / ODA  Özel Durum Açıklaması  (sözleşme, yatırım, haber/söylenti, insider)
    FR         Finansal Rapor          (bilanço, faaliyet raporu, sorumluluk beyanı)
    DKB        Diğer Bildirimler       (devre kesici, MKK kupon/ÖPY, KAP duyurusu)

DİKKAT: API JSON'da sınıf "ODA" gelirken, HTML tabloda "ÖDA" görülür.
Karşılaştırma daima `tr_lower` + Türkçe karakter eşlemesi ile yapılır.
"""

from __future__ import annotations

from enum import Enum

from .parsing.text import tr_lower

CLASS_ODA = "oda"
CLASS_FR = "fr"
CLASS_DKB = "dkb"

_CLASS_ALIASES = {
    "öda": CLASS_ODA,
    "oda": CLASS_ODA,
    "özel durum açıklaması": CLASS_ODA,
    "özel durum": CLASS_ODA,
    "fr": CLASS_FR,
    "finansal rapor": CLASS_FR,
    "dkb": CLASS_DKB,
    "diğer bildirimler": CLASS_DKB,
}


class Route(str, Enum):
    """Bir bildirimin yazgısı. `disclosure.status` alanına birebir yazılır."""

    PIPELINE = "pipeline"
    """Skorlanır, eşiği geçerse bildirilir."""

    FUNDAMENTALS_REFRESH = "fundamentals_refresh"
    """Finansal rapor: push edilmez; finansal veri tazelemesini TETİKLER + özete girer."""

    ROUTINE = "routine"
    """Rutin: kaydedilir, skorlanmaz."""

    MECHANICAL = "mechanical"
    """Mekanik (devre kesici vb.): kaydedilir, skorlanmaz ama SAYILIR —
    aynı hissede gün içi 3+ devre kesici olağandışı harekettir (ayrı uyarı)."""


# ÖDA içinde rutin sayılan konular (tr_lower ile karşılaştırılır: parça eşleşmesi)
_ROUTINE_SUBJECTS: tuple[str, ...] = (
    "pay dökümü",
    "şirket genel bilgi formu",
    "genel kurul işlemlerine ilişkin",
    "genel kurul",
    "kayıtlı sermaye tavanı",
    "sorumluluk beyanı",
    "faaliyet raporu",
    "pay dışında sermaye piyasası aracı işlemlerine ilişkin",  # kupon/itfa
    "borsa dışı repo",
    "portföy dağılım raporu",
)

# DKB içinde mekanik ama bilgi değeri taşımayan kalıplar
_MECHANICAL_PATTERNS: tuple[str, ...] = (
    "devre kesici",
    "borçlanma araçları, yatırım fonları ve varant i̇tfa",
    "borçlanma araçları, yatırım fonları ve varant itfa",
    "varantların veya sertifikaların i̇şlem görmeye başlaması",
    "varantların veya sertifikaların işlem görmeye başlaması",
)

# DKB olmasına rağmen ÖNEMLİ olan istisnalar (SPK II-15.1 md.12-4 duyurusu vb.)
_IMPORTANT_DKB_PATTERNS: tuple[str, ...] = (
    "kamuyu aydınlatma platformu duyurusu",
)


def normalize_class(raw: str | None) -> str | None:
    """'ÖDA' / 'ODA' / 'oda' / 'Özel Durum Açıklaması' -> 'oda'."""
    if not raw:
        return None
    return _CLASS_ALIASES.get(tr_lower(raw).strip())


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack for n in needles)


def route(
    disclosure_class: str | None,
    subject: str | None,
    summary: str | None = None,
) -> Route:
    """Bir bildirimi en ucuz bilgiyle sınıflandırır. Ağ/DB erişimi yok.

    Fail-open: sınıf tanınmıyorsa PIPELINE'a düşer (kaçırmak, gürültüden pahalı).
    """
    cls = normalize_class(disclosure_class)
    subject_n = tr_lower(subject or "")
    summary_n = tr_lower(summary or "")

    if cls == CLASS_ODA:
        if _contains_any(subject_n, _ROUTINE_SUBJECTS):
            return Route.ROUTINE
        return Route.PIPELINE

    if cls == CLASS_FR:
        return Route.FUNDAMENTALS_REFRESH

    if cls == CLASS_DKB:
        if _contains_any(subject_n, _IMPORTANT_DKB_PATTERNS):
            return Route.PIPELINE
        if _contains_any(subject_n, _MECHANICAL_PATTERNS) or _contains_any(
            summary_n, _MECHANICAL_PATTERNS
        ):
            return Route.MECHANICAL
        return Route.ROUTINE

    # Sınıf YOK: /api/disclosure/list/light bu alanı hiç vermiyor (canlı doğrulandı).
    # O yüzden konu/özet üzerinden mekanik ve rutin kalıpları yine de yakalarız;
    # hiçbir kalıba uymuyorsa fail-open (kaçırmak, gürültüden pahalı).
    if _contains_any(subject_n, _IMPORTANT_DKB_PATTERNS):
        return Route.PIPELINE
    if _contains_any(subject_n, _MECHANICAL_PATTERNS) or _contains_any(
        summary_n, _MECHANICAL_PATTERNS
    ):
        return Route.MECHANICAL
    if _contains_any(subject_n, _ROUTINE_SUBJECTS):
        return Route.ROUTINE
    return Route.PIPELINE


_FUND_MARKERS: tuple[str, ...] = ("portföy", "portfoy", "yatırım fonu", "yatirim fonu", " serbest fon")


def looks_like_fund(title: str) -> bool:
    """Fon/portföy bildirimi mi? (Hisse senedi değil -> unresolved kuyruğunu kirletmesin)

    KAP akışının büyük kısmı fon bildirimi (canlı örnekte 9 kaydın 2'si).
    Bunları "eşleşmeyen hisse" saymak, gerçekten eşleşmeyen hisseleri gürültü
    içinde kaybettirir. Ayrı durum: `filtered_fund`.
    """
    text = tr_lower(title or "")
    if any(marker in text for marker in _FUND_MARKERS):
        return True
    return text.rstrip().endswith("fonu") or " fon " in text


def should_push(route_result: Route) -> bool:
    """v1 bildirim politikası: yalnızca PIPELINE anında bildirim adayıdır."""
    return route_result is Route.PIPELINE
