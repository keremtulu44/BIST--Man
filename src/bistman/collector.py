"""Sessiz KAP bağlam toplayıcısı — haber geldiğinde yalnız doğrula ve arşivle.

Alarm üretmez, mesaj göndermez, grafik/fiyat indirmez, ayrı yorum çağrısı açmaz.
Tek bir ortak `/list/light` okuması yapar; ilk elemeden geçen adayları tek hisseye
bağlar, `/attachment-detail/{id}` ile kimlik ve kanıtı doğrular, `AlarmArchive`
içine gerçek gözlem zamanıyla (`known_at`) sessizce yazar.

Tek uygulama / tek Render dağıtımı için `KapCollectorService`:
  - Şirket unvan/OID listesini aynı SQLite dosyasında önbellekler (günde 1 yeniler,
    geçici KAP hatasında bayat önbellekle kesintisiz devam eder).
  - İsteğe bağlı başlangıç ısınması (`warmup`) ile restart sonrası izlenen hisselerin
    son penceredeki bildirimlerini tarar.
  - Eski `alarm_seen` / `alarm_scans` / sürüm kayıtlarını periyodik olarak budar.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .alarm_context import DEFAULT_MAX_AGE, AlarmArchive, ReviewedReading, open_archive
from .context_packet import MAX_CHARS, Origin, Topic, _starts_with_lowercase_word, make_candidate
from .fundamentals import PublishedRow
from .parsing.dates import TRT, to_trt
from .parsing.evidence import (
    issuer_signature,
    sale_qualified,
    stoppage_confirmed,
    stoppage_contradicted,
)
from .parsing.kap_table import body_text
from .parsing.text import is_abbreviation_break, symbols_in_title, tr_lower
from .sources.base import DisclosureDetail, DisclosureStub, ParseResult
from .sources.kap import KapClient, KapError
from .sources.kap_companies import (
    CompanyEntry,
    CompanyMapError,
    build_company_resolver,
    fetch_companies,
    select_primary_ticker,
)
from .ticker_map import TickerResolver
from .triage import Route, is_index_agenda, looks_like_fund, route

__all__ = [
    "DetailFetcher",
    "EvidenceProposal",
    "EvidenceReviewer",
    "KapCollectorService",
    "ScanReport",
    "extract_verified_evidence",
    "ingest_feed",
    "is_low_signal_corporate",
    "is_non_equity_disclosure",
    "sync_once",
]

DetailFetcher = Callable[[int], Awaitable[DisclosureDetail]]

# Tekrar taramada yeniden detay indirme gerektirmeyen durumlar.
_TERMINAL_SEEN = frozenset({
    "hazir",
    "filtered_routine",
    "filtered_mechanical",
    "fundamentals_refresh",
    "filtered_non_equity",
    "filtered_low_signal",
    "agenda",
    "cok_hisseli",
    "dis_evren",
    "kapsam_disi",
    "geri_cekildi",
    "satis_kaniti_yetersiz",
    "durus_kaniti_yetersiz",
    "bedel_baglamla_celisiyor",
    "inceleme_bekleniyor",
    "ihale_sonucu_icin_detay_gerekli",
})

_NON_EQUITY_SUBJECTS: tuple[str, ...] = (
    "fon sürekli bilgilendirme formu",
    "fon ihraç sözleşmesi",
    "fon içtüzüğü",
    "yatırım kuruluşu varant - sertifika - senetlerine i̇lişkin bildirim",
    "yatırım kuruluşu varant - sertifika - senetlerine ilişkin bildirim",
    "yapılandırılmış yatırım araçları",
    "borsa yatırım fonlarına ilişkin",
)
_NON_EQUITY_SUMMARY_MARKERS: tuple[str, ...] = (
    "yatırım fonlarına ilişkin rehber",
    "yatırım fonu katılma pay",
)

# İdari/bürokratik bildirimler: hisse alarmında olay bağlamı taşımaz,
# gereksiz detay isteği ve sahte 'detay_bekleniyor' üretmemesi için ayrılır.
_LOW_SIGNAL_SUBJECTS: tuple[str, ...] = (
    "herhangi bir otoriteye mali tablo verilmesi",
    "bağımsız denetim kuruluşunun belirlenmesi",
    "kurumsal yönetim bilgi formu",
    "kurumsal yönetim ilkelerine uyum",
    "sürdürülebilirlik uyum",
    "yönetim kurulu komiteleri",
    "şirket merkezi değişikliği",
    "iletişim bilgileri",
    "likidite sağlayıcılık kapsamındaki işlemler",
    "sermaye artırımından elde edilecek - edilen fonun kullanımına ilişkin rapor",
    "eski payların yenileriyle değiştirilmesi",
)

# Liste satırında şirket adı yerine kurum adı taşıyan bildirimler:
# hedef hisse kodu yalnız detaydaki `stockCode` / `relatedStocks` içindedir.
_OPAQUE_ISSUERS = frozenset({
    "kamuyu aydınlatma platformu",
    "borsa istanbul a.ş.",
    "sermaye piyasası kurulu",
    "merkezi kayıt kuruluşu a.ş.",
})

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_MAX_QUOTE_LEN = MAX_CHARS - 180

# Standart KAP şablon kapanış/giriş cümleleri: asla olay kanıtı olarak seçilmez.
_BOILERPLATE_RE = re.compile(
    r"(?:yukarıdaki\s+açıklamalarımızın\s+sermaye\s+piyasası"
    r"|kamuoyuna\s+(?:ve\s+yatırımcılarımıza\s+)?saygıyla\s+duyurulur"
    r"|tasarruf\s+sahiplerinin\s+ve\s+kamuoyunun\s+bilgisine"
    r"|yatırımcılarımızın\s+ve\s+kamuoyunun\s+bilgisine\s+sunulur"
    r"|aşağıdaki\s+açıklamanın\s+yapılmasına\s+gerek\s+duyulmuştur"
    r"|işbu\s+açıklamamızın\s+ingilizce\s+çeviri"
    r"|çeviri\s+metin\s+arasında\s+herhangi\s+bir\s+farklılık"
    r"|toplantısında\s+aşağıdaki\s+kararlar\s+alınmıştır)",
)

# --- Doğrulanabilir BIST şirket açıklaması kalıpları (yalnız birebir cümle seçimi) ---

# Hem edilgen ("karar verildi/verilmiştir") hem etken ("karar verdi/vermiştir") YK kalıbı.
_DECIDED = r"karar\s+ver(?:il)?(?:di|miştir)"

# Olumsuzlanmış, henüz plan aşamasındaki veya yalanlanan ifadeler olumlu karar sayılmaz.
# Not: "kâr payı dağıtılmamasına karar verilmiştir" gerçek bir YK kararı olduğu için korunur.
_NEGATED_DECISION_RE = re.compile(
    r"(?:\bkarar\s+ver(?:il)?me(?:di|miştir)"
    r"|\bonaylanma(?:dı|mıştır)|\bkabul\s+edilme(?:di|miştir)"
    r"|\bbaşlatılma(?:dı|mıştır|masına)|\byapılmamasına\s+karar"
    r"|\b(?:satın\s+)?alınmamasına\s+karar|\bsatılmamasına\s+karar"
    r"|\bvazgeçilme(?:di|miştir)"
    r"|\buygulanma(?:dı|mıştır|maktadır|yacak\w*)"
    r"|\bkesilme(?:di|miştir)|\bverilme(?:di|miştir)|\bhükmedilme(?:di|miştir)"
    r"|\bdevreye\s+alınma(?:dı|mıştır)|\biptal\s+edilme(?:di|miştir)|\bfeshedilme(?:di|miştir)"
    r"|\b(?:planlanmakta|hedeflenmekte|öngörülmekte|değerlendirilmekte)\w*"
    r"|\bsöz\s+konusu\s+değil\w*|\bgerçeği\s+yansıtm)",
)

_CONTRACT_TERMINATION_RE = re.compile(
    r"\b(?:sözleşme\w*|anlaşma\w*|sipariş\w*)\s+.{0,90}?\b"
    r"(?:feshedil(?:di|miştir)|fesh\s*edil(?:di|miştir)|iptal\s+edil(?:di|miştir)|sona\s+erdiril(?:di|miştir))\b",
)

_TENDER_AWARD_RE = re.compile(
    r"(?:\bihale\w*\s+(?:(?:\w+\s+){0,4})?uhdemizde\s+kal(?:dı|mıştır)"
    r"|\bşirketimiz\s+lehine\s+sonuçlan(?:dı|mıştır)"
    r"|\ben\s+avantajlı\s+teklif\w*\s+.+?\bşirketimiz(?:ce|\s+tarafından)\s+veril(?:di|miş)"
    r"|\bihale(?:yi)?\s+(?:(?:\w+\s+){0,4})?(?:şirketimiz\s+)?kazan(?:dı|mış|mıştır)"
    r"|\bkesinleşen\s+ihale\s+kararı\s+(?:şirketimize\s+)?tebliğ\s+edil(?:di|miştir)"
    r"|\bsözleşme\s+(?:imzalanmasına\s+ilişkin\s+)?davet\w*\s+(?:şirketimize\s+|tarafımıza\s+)?(?:ulaş|tebliğ\s+edil|alın|gönderil)(?:dı|di|mıştır|miştir))",
)
_TENDER_DISQUALIFY_RE = re.compile(
    r"(?:\buhdemizde\s+kalma(?:dı|mıştır)|\bihale\w*\s+(?:(?:\w+\s+){0,3})?iptal\s+edil(?:di|miştir)"
    r"|\bkazanama(?:dı|mıştır)|\bdeğerlendirme\s+süreci\s+devam\s+etmekte|\belenmiş|\bgeçersiz\s+sayıl"
    r"|\byürürlüğe\s+girmemiş|\bgerçeği\s+yansıtm)",
)

_CAPITAL_ACTION_RE = re.compile(
    rf"(?:\b(?:çıkarılmış|ödenmiş)\s+sermaye\w*\s+.+?\b"
    rf"(?:(?:bedelsiz|bedelli|tahsisli)\s+.*?\b(?:{_DECIDED}|artırıl(?:dı|mıştır)|başvuru\w*\s+yapıl(?:dı|mıştır)|onaylan(?:dı|mıştır))"
    rf"|(?:artırılmasına|azaltılmasına)\s+{_DECIDED})"
    rf"|\b(?:bedelsiz|bedelli|tahsisli)\s+sermaye\s+artırım\w*\s+.+?\b"
    rf"(?:{_DECIDED}|başvuru\w*\s+yapıl(?:dı|mıştır)|başvuru\s+yap(?:tı|mıştır)"
    rf"|onaylan(?:dı|mıştır)|reddedil(?:di|miştir)|iptal\s+edil(?:di|miştir)))",
)

_DIVIDEND_RE = re.compile(
    rf"(?:\b(?:nakit\s+|pay\s+biçiminde\s+)?(?:k[aâ]r\s+payı|temettü)\s+"
    rf"(?:dağıtılmasına|dağıtılmamasına|ödenmesine|dağıtılmasının)\s+.{{0,80}}?\b"
    rf"(?:{_DECIDED}|onayına\s+sunulmasına\s+{_DECIDED})"
    rf"|\bpay\s+başına\s+(?:brüt|net)\s+\d[\d.,]*\s*(?:tl|kr|kuruş)\s+(?:nakit\s+)?(?:k[aâ]r\s+payı|temettü))",
)

_BUYBACK_RE = re.compile(
    rf"(?:\bpay(?:larımızın|larının)?\s+geri\s+alınmasına\s+.{{0,120}}?\b{_DECIDED}"
    rf"|\bpay(?:larımızın|larının)?\s+geri\s+alınmasına\s+{_DECIDED}"
    rf"|\bgeri\s+alım\s+programı\w*\s+.{{0,100}}?\b"
    rf"(?:başlatıl(?:dı|mıştır)|başlatılmasına\s+{_DECIDED}|sonlandırıl(?:dı|mıştır)"
    rf"|sonlandırılmasına\s+{_DECIDED}|tamamlan(?:dı|mıştır)|kabul\s+edil(?:di|miştir)|{_DECIDED})"
    rf"|\bgeri\s+alım\s+işlemi\s+gerçekleştiril(?:di|miştir))",
)
# Günlük geri alım işlemi bildirimlerinde önceki YK kararına atıf ("kararı kapsamında ... pay geri alınmıştır")
_BUYBACK_DAILY_TX_RE = re.compile(
    r"\b[\d.,]+\s*(?:adet|tl\s+nominal|nominal(?:\s+değerli)?)\s+(?:\w+\s+){0,3}pay\w*\s+geri\s+alın(?:dı|mıştır)"
)

_REGULATORY_MEASURE_RE = re.compile(
    rf"(?:\baçığa\s+satışa?\s+ve\s+kredili\s+işlem\w*"
    rf"|\bbrüt\s+takas\s+(?:tedbiri\s+)?(?:uygulan\w*|sona\s+er(?:di|miştir)|kaldırıl(?:dı|mıştır))"
    rf"|\bemir\s+paketi\s+tedbiri\s+(?:uygulan\w*|sona\s+er(?:di|miştir)|kaldırıl(?:dı|mıştır))"
    rf"|\btek\s+fiyat\s+işlem\s+yöntemi\s+(?:uygulan\w*|sona\s+er(?:di|miştir)|kaldırıl(?:dı|mıştır))"
    rf"|\b(?:pay\s+sırası|işlem\s+sırası)\s+.{{0,80}}?\b(?:işleme\s+kapatıl(?:dı|mıştır)|durdurul(?:du|muştur)|yeniden\s+işleme\s+açıl(?:dı|mıştır))"
    rf"|\b(?:geçici\s+)?işlem\s+yasağı\s+(?:uygulan(?:dı|mıştır|acaktır)|getiril(?:di|miştir)|kararı\s+(?:alın|veril)(?:dı|di|mıştır|miştir))"
    rf"|\bidari\s+para\s+cezası\s+(?:uygulan(?:dı|mıştır|masına)|veril(?:di|miştir|mesine)|kesil(?:di|miştir|mesine))(?:\s+{_DECIDED})?)",
)

# Haber/söylenti açıklamalarında şirketin doğrudan netleştirici/sonuç cümlesi.
_RUMOUR_CLARIFICATION_RE = re.compile(
    r"(?:\b(?:idari\s+para\s+cezası|işlem\s+yasağı|yaptırım\s+kararı)\s+bulunmamaktadır"
    r"|\bgerçeği\s+yansıtmamaktadır\b|\basılsızdır\b|\bdoğru\s+değildir\b"
    r"|\bherhangi\s+bir\s+(?:\w+\s+){0,4}(?:karar\w*|girişim\w*|görüşme\w*|sözleşme\w*|yaptırım\w*|ceza\w*)\s+bulunmamaktadır"
    r"|\biddialarda\s+adı\s+geçen\s+.+?\byoktur\b"
    r"|\b(?:görüşmeler|değerlendirmeler)\s+devam\s+etmektedir\b)",
)

_STRATEGIC_ACTION_RE = re.compile(
    rf"(?:\b(?:taşınmaz\w*|gayrimenkul\w*|fabrika\w*|tesis\w*|arsa\w*|iştirak\w*|bağlı\s+ortaklık\w*|pay\w*)\s+"
    rf".+?\b(?:satılmasına|satın\s+alınmasına|devralınmasına|devredilmesine"
    rf"|satın\s+alınmasından\s+vazgeçilmesine|satışından\s+vazgeçilmesine)\s+{_DECIDED}"
    rf"|\byatırım\s+teşvik\s+belgesi\s+(?:alın(?:dı|mıştır)|onaylan(?:dı|mıştır))"
    rf"|\b(?:yeni\s+(?:\w+\s+){{0,4}})?(?:üretim\s+tesisi\w*|tesisi|fabrikası|üretim\s+hattı\w*|santral\w*|kapasite\s+artış\w*)\s*"
    rf"(?:.{{0,80}}?\s)?(?:devreye\s+alın(?:dı|mıştır)|yatırım\w*\s+karar\w*\s+alın(?:dı|mıştır)|{_DECIDED})"
    rf"|\b(?:birleşme|bölünme|halka\s+arz)\s+(?:işlem\w*|başvuru\w*)\s+.+?\b"
    rf"(?:{_DECIDED}|başvuru\w*\s+yapıl(?:dı|mıştır)|onaylan(?:dı|mıştır)|reddedil(?:di|miştir))"
    rf"|\b(?:grev|lokavt)\s+kararı\s+alın(?:dı|mıştır)"
    rf"|\bşirketimiz(?:in)?\s+.{{0,60}}?\blisans\w*\s+(?:iptal\s+edil(?:di|miştir)|veril(?:di|miştir)|yenilen(?:di|miştir))"
    rf"|\bşirketimiz\s+.{{0,80}}?\bkonkordato\s+(?:talebinde\s+bulun(?:du|muştur)|ilan\s+et(?:ti|miştir)|mühleti\s+(?:al(?:dı|mıştır)|veril(?:di|miştir)))"
    rf"|\bşirketimiz(?:in)?\s+.{{0,80}}?\b(?:ciro|hasılat|favök|satış|yatırım|büyüme)\s+(?:beklenti\w*|hedef\w*)\s+.{{0,60}}?\b(?:revize\s+et(?:ti|miştir)|revize\s+edil(?:di|miştir)|güncelle(?:di|miştir)|güncellen(?:di|miştir))"
    rf"|\b(?:uzun\s+vadeli\s+(?:ulusal|yabancı\s+para)|kredi\s+derecelendirme)\s+(?:kredi\s+derecelendirme\s+)?notu\w*\s+.+?\b"
    rf"(?:teyit\s+et(?:ti|miştir)|teyit\s+edil(?:di|miştir)|belirlen(?:di|miştir)|yükseltil(?:di|miştir)|düşürül(?:dü|müştür)|revize\s+edil(?:di|miştir)))",
)

_INSIDER_DISCLOSURE_RE = re.compile(
    r"(?:\bsermayesindeki\s+paylarım(?:/oy\s+haklarım)?\s+.+?\b"
    r"(?:ulaşmıştır|aşmıştır|altına\s+düşmüştür|olmuştur)"
    r"|\btoplam\s+[\d.,]+\s*(?:tl\s+)?toplam\s+nominal\s+tutarlı\s+alış\s+(?:ve/veya\s+[\d.,]+\s*(?:tl\s+)?toplam\s+nominal\s+tutarlı\s+satış\s+)?işlemi\s+gerçekleştirilmiştir"
    r"|\b[\d.,]+\s*(?:tl\s+)?nominal\s+tutarlı\s+(?:alış|satış)\s+işlemi\s+(?:tarafımca\s+|şirketimizce\s+)?gerçekleştirilmiştir)",
)

_TENDER_NOTE = "İhale sonucu bildirildi; kesin sözleşme imzası ayrıca izlenmelidir."


@dataclass(frozen=True, slots=True)
class EvidenceProposal:
    """Doğrulanmış kaynaktan seçilen konu ve tam cümle alıntısı."""

    topic: Topic
    quote: str
    reviewed: ReviewedReading | None = None
    supersedes: int | None = None
    valid_until: datetime | None = None


class EvidenceReviewer(Protocol):
    """İsteğe bağlı inceleyici: önerisi yine `AlarmArchive.record` kurallarından geçer."""

    async def propose(
        self,
        disclosure: DisclosureStub,
        ticker: str,
        detail: DisclosureDetail | None,
        body: str,
    ) -> EvidenceProposal | None: ...


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Tek tarama turunun denetlenebilir özeti (kullanıcı mesajı değil)."""

    total_rows: int
    rejected_rows: int
    counts: dict[str, int]
    detail_requests: int = 0
    unprobed_opaque: int = 0
    fundamentals_refresh: tuple[str, ...] = field(default_factory=tuple)
    scanned_until: datetime | None = None
    suspicious_empty: bool = False


def is_non_equity_disclosure(title: str, subject: str, summary: str | None = None) -> bool:
    """Yatırım fonu, varant ve ikraz iştirak senedi gibi hisse dışı bildirim mi?"""
    if looks_like_fund(title):
        return True
    subj = tr_lower(subject or "")
    if any(marker in subj for marker in _NON_EQUITY_SUBJECTS):
        return True
    if summary:
        summ = tr_lower(summary)
        if any(marker in summ for marker in _NON_EQUITY_SUMMARY_MARKERS):
            return True
    return False


def is_low_signal_corporate(subject: str) -> bool:
    """Rutin/idari kurumsal form bildirimi mi (alarm bağlamı taşımaz)?"""
    subj = tr_lower(subject or "")
    return any(marker in subj for marker in _LOW_SIGNAL_SUBJECTS)


def _is_opaque_issuer(title: str) -> bool:
    return tr_lower((title or "").strip()) in _OPAQUE_ISSUERS


def _iter_sentence_pairs(text: str) -> list[tuple[str, str]]:
    """`body_text` veya özetten `(tam_cumle, tr_lower(tam_cumle))` çiftlerini O(N) çıkarır."""
    if not text:
        return []
    pairs: list[tuple[str, str]] = []
    for paragraph in text.splitlines():
        line = paragraph.strip()
        if not line:
            continue
        llen = len(line)
        start = 0
        for match in _SENTENCE_END.finditer(line):
            end = match.end()
            if end < llen and is_abbreviation_break(
                line[max(0, end - 128):end],
                line[end:min(llen, end + 64)],
            ):
                continue
            cand = line[start:end].strip()
            start = end
            if (
                not cand
                or len(cand) > _MAX_QUOTE_LEN
                or _starts_with_lowercase_word(cand)
            ):
                continue
            low = tr_lower(cand)
            if not _BOILERPLATE_RE.search(low):
                pairs.append((cand, low))
    return pairs


def _first_matching(
    sentences: Sequence[tuple[str, str]],
    pattern: re.Pattern[str],
    *,
    reject_negated: bool = True,
) -> str | None:
    for sentence, low in sentences:
        if pattern.search(low):
            if reject_negated and _NEGATED_DECISION_RE.search(low):
                continue
            return sentence
    return None


def extract_verified_evidence(
    disclosure: DisclosureStub,
    body: str = "",
) -> EvidenceProposal | None:
    """Kural tabanlı, uydurmasız kanıt seçici.

    Her öneri kaynak metindeki birebir tam cümleye dayanır; yön/neden uydurmaz,
    olumsuzlanmış veya şarta bağlı ifadeleri olumlu olay gibi sunmaz. Cümleler
    tek geçişte (O(N)) ayrıştırılır. Şüphede `None` döndürür ve aday
    `detay_bekleniyor` olarak kalır.
    """
    summary = (disclosure.summary or "").strip()
    sources = [s for s in (body, summary) if s and s != disclosure.subject.strip()]
    if not sources:
        return None
    sentences: list[tuple[str, str]] = []
    for source in sources:
        sentences.extend(_iter_sentence_pairs(source))
    if not sentences:
        return None

    all_fields = (disclosure.subject, summary, body)
    subj_low = tr_lower(disclosure.subject or "")
    full_low = tr_lower(" ".join(f for f in all_fields if f))
    qualified_sale = sale_qualified(*all_fields)

    # 1a) Doğrulanmış, şartsız/iptalsiz imzalı satış sözleşmesi veya kesin sipariş
    if not qualified_sale:
        for sentence, _low in sentences:
            if issuer_signature(sentence) == "sale":
                return EvidenceProposal(topic="satis_sozlesmesi", quote=sentence)

    # 1b) Açık sözleşme feshi/iptali: önemli olumsuz gelişme, birebir `sirket_aciklamasi` olarak korunur
    # (aynı bildirim numarasında eski bir satışı da güncel gözlem anından itibaren değiştirir).
    if "ihale" not in subj_low:
        if hit := _first_matching(sentences, _CONTRACT_TERMINATION_RE):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 1c) Şarta bağlı / imzalanmamış / mutabakat zaptı satış adayı: olumlu satış gibi yayımlanmaz.
    if qualified_sale and (
        any(k in subj_low for k in ("sözleşme", "satış", "yeni iş"))
        or any(issuer_signature(s) == "sale" for s, _ in sentences)
    ):
        for sentence, _low in sentences:
            if sale_qualified(sentence) or issuer_signature(sentence) == "sale":
                return EvidenceProposal(topic="satis_sozlesmesi", quote=sentence)

    # 2) Doğrulanmış üretim / faaliyet duruşu (veya çelişen/yeniden başlayan güncelleme)
    contradicted_stop = stoppage_contradicted(*all_fields)
    if not contradicted_stop:
        for sentence, _low in sentences:
            if stoppage_confirmed(sentence):
                return EvidenceProposal(topic="uretim_durusu", quote=sentence)

    if contradicted_stop and any(k in subj_low for k in ("üretim", "duruş", "faaliyet")):
        for sentence, _low in sentences:
            if stoppage_contradicted(sentence):
                return EvidenceProposal(topic="uretim_durusu", quote=sentence)

    # 3) Kesinleşen ihale kazanımı / sözleşme daveti (yalnız teklif veya iptal elenir)
    if "ihale" in subj_low and not _TENDER_DISQUALIFY_RE.search(full_low):
        if hit := _first_matching(sentences, _TENDER_AWARD_RE):
            note = (
                ReviewedReading(hit, _TENDER_NOTE)
                if len(hit) + len(_TENDER_NOTE) <= 400
                and not sale_qualified(hit)
                else None
            )
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit, reviewed=note)

    # 4) Sermaye artırımı / azaltımı (bedelsiz, bedelli, tahsisli karar/başvuru/onay/ret)
    if any(k in subj_low for k in ("sermaye artırım", "sermaye azaltım", "bedelsiz", "bedelli", "tahsisli")):
        if hit := _first_matching(sentences, _CAPITAL_ACTION_RE):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 5) Kâr payı / temettü dağıtım (veya dağıtmama) kararı
    if any(k in subj_low for k in ("kar payı", "kâr payı", "kar dağıtım", "kâr dağıtım", "temettü")):
        if hit := _first_matching(sentences, _DIVIDEND_RE):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 6) Pay geri alım programı başlatma/sonlandırma/revizyon kararı
    # (önceki karara atıflı rutin günlük adet bildirimleri program kararı sayılmaz).
    if "geri alınmasına" in subj_low or "geri alım" in subj_low:
        if hit := _first_matching(sentences, _BUYBACK_RE):
            if not ("kapsamında" in tr_lower(hit) and _BUYBACK_DAILY_TX_RE.search(tr_lower(hit))):
                return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 7) VBTS / SPK işlem yasağı / pay sırası kapatma / idari para cezası
    if any(
        k in subj_low
        for k in ("vbts", "tedbir", "işlem yasağı", "işleme kapatılma", "işleme açılma", "işlem sırası", "idari para cezası")
    ):
        if hit := _first_matching(sentences, _REGULATORY_MEASURE_RE):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 8) Haber ve söylentilere ilişkin açıklama (MTRKS #1699030 dahil: birebir sonuç cümlesi)
    if "haber ve söylenti" in subj_low or "haber veya söylenti" in subj_low:
        if hit := _first_matching(sentences, _RUMOUR_CLARIFICATION_RE, reject_negated=False):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 9) Pay alım satım bildirimi (önemli ortak/yönetici pay eşiği veya işlem özeti)
    if "pay alım satım" in subj_low or "pay alım-satım" in subj_low:
        if hit := _first_matching(sentences, _INSIDER_DISCLOSURE_RE):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    # 10) Genel ÖDA ve stratejik kararlar (varlık alım/satım, tesis, grev, lisans, konkordato,
    # beklenti revizyonu, kredi notu, birleşme, sermaye, temettü, geri alım, tedbir, yalanlama)
    for pattern, guard in (
        (_STRATEGIC_ACTION_RE, True),
        (_CAPITAL_ACTION_RE, True),
        (_DIVIDEND_RE, True),
        (_BUYBACK_RE, True),
        (_REGULATORY_MEASURE_RE, True),
        (_RUMOUR_CLARIFICATION_RE, False),
    ):
        if hit := _first_matching(sentences, pattern, reject_negated=guard):
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit)

    if not _TENDER_DISQUALIFY_RE.search(full_low):
        if hit := _first_matching(sentences, _TENDER_AWARD_RE):
            note = (
                ReviewedReading(hit, _TENDER_NOTE)
                if len(hit) + len(_TENDER_NOTE) <= 400
                and not sale_qualified(hit)
                else None
            )
            return EvidenceProposal(topic="sirket_aciklamasi", quote=hit, reviewed=note)

    return None


def _fingerprint(stub: DisclosureStub) -> bytes:
    payload = [
        stub.index,
        stub.published_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        stub.title,
        stub.subject,
        stub.summary,
        stub.disclosure_class,
        list(stub.tickers),
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).digest()


def _collapse_company_codes(
    codes: Sequence[str],
    code_to_oid: Mapping[str, str],
    allowed: set[str] | None = None,
    *,
    member_oid: str | None = None,
) -> tuple[tuple[str, ...], bool]:
    """Hisse kodlarını tek ihraççıya indirger; farklı şirketlerse `((), True)` döndürür.

    `GARAN, TGB`, `HALKB, THB`, `AKBNK, TAB`, `VAKBN, TVB`, `SISE, TSC`,
    `ISA, ISB, ISCTR, ISKUR`, `KRDMA, KRDMB, KRDMD` gibi aynı `mkkMemberOid`
    altındaki kodlar tek şirkettir; `ASELS, THYAO` gibi ayrı şirketler `cok_hisseli` kalır.
    """
    unique = tuple(dict.fromkeys(c.strip().upper() for c in codes if c and c.strip()))
    if not unique:
        return (), False
    if len(unique) == 1:
        return unique, False
    if code_to_oid:
        oids = [code_to_oid.get(c) for c in unique]
        if all(oids) and len(set(oids)) == 1:
            common_oid = oids[0]
            if member_oid and member_oid != common_oid:
                return (), True
            if allowed is not None:
                watched = tuple(c for c in unique if c in allowed)
                if watched:
                    return watched, False
            primary = select_primary_ticker(unique)
            return ((primary,) if primary else (unique[0],)), False
    return (), True


def _resolve_single_ticker(
    stub: DisclosureStub,
    resolver: TickerResolver,
    *,
    code_to_oid: Mapping[str, str],
    oid_to_codes: Mapping[str, tuple[str, ...]],
    allowed: set[str] | None = None,
) -> tuple[tuple[str, ...], bool]:
    """`(hedef_kodlar, cok_sirketli_mi)` döndürür. Çok şirketli kayıt asla tek hisseye yazılmaz."""
    if stub.tickers:
        return _collapse_company_codes(
            stub.tickers, code_to_oid, allowed, member_oid=stub.member_oid,
        )
    if stub.member_oid and oid_to_codes and not _is_opaque_issuer(stub.title):
        known = oid_to_codes.get(stub.member_oid)
        if known:
            return _collapse_company_codes(known, code_to_oid, allowed, member_oid=stub.member_oid)
        # BIST şirket listesinde olmayan ihraççı OID'i (ör. borsada işlem görmeyen aracı kurum)
        return (), False
    symbols = tuple(dict.fromkeys(symbols_in_title(stub.title)))
    if len(symbols) > 1:
        return _collapse_company_codes(symbols, code_to_oid, allowed)
    hit = resolver.resolve(stub.title).ticker
    if hit:
        return (hit,), False
    if symbols and resolver.has_ticker(symbols[0]):
        return (symbols[0],), False
    return (), False


def _detail_for_ticker(detail: DisclosureDetail, ticker: str) -> DisclosureDetail:
    """Aynı `mkkMemberOid` altındaki çok kodlu detayı doğrulanan tek pay koduna daraltır."""
    if detail.stub.tickers == (ticker,) or not detail.stub.tickers:
        return detail
    narrowed = DisclosureStub(
        index=detail.stub.index,
        published_at=detail.stub.published_at,
        title=detail.stub.title,
        subject=detail.stub.subject,
        summary=detail.stub.summary,
        disclosure_class=detail.stub.disclosure_class,
        tickers=(ticker,),
        member_oid=detail.stub.member_oid,
        minute_precision=detail.stub.minute_precision,
    )
    return DisclosureDetail(
        stub=narrowed,
        body_html=detail.body_html,
        attachment_count=detail.attachment_count,
    )


def _now_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.utcoffset() is None:
        raise ValueError("clock saat dilimli datetime döndürmeli")
    return value.astimezone(timezone.utc)


async def ingest_feed(
    feed: ParseResult,
    archive: AlarmArchive,
    *,
    resolver: TickerResolver,
    detail_fetcher: DetailFetcher | None = None,
    reviewer: EvidenceReviewer | None = None,
    statements: Mapping[str, Sequence[PublishedRow]] | None = None,
    universe: Sequence[str] | None = None,
    companies: Sequence[CompanyEntry] = (),
    origin: Origin = "kap_arsiv",
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    probe_unresolved: bool = False,
    probe_opaque: bool = False,
    max_unresolved_probes: int = 25,
    strict_watermark: bool = False,
    update_watermark: bool = True,
) -> ScanReport:
    """Ayrıştırılmış KAP listesini sessizce doğrular ve `archive`'e işler.

    - `companies`: BIST şirket `(ticker, oid, unvan)` listesi verildiğinde hem aynı
      `mkkMemberOid` altındaki çok kodlu tek şirketler (`GARAN, TGB`, `ISA..ISCTR`,
      `KRDMA..KRDMD`) doğru paya indirgenir hem de borsada işlem görmeyen ihraççı
      OID'lerinin BIST hisselerine yanlış bağlanması engellenir.
    - `probe_opaque=True`: başlığı `KAMUYU AYDINLATMA PLATFORMU` / `SPK` / `BİST`
      olan hisse kodsuz satırların detayını çekip gerçek hisseyi bulur.
    - `probe_unresolved=True`: unvanı eşleşmeyen diğer kurumsal satırları da denetler.
    - `strict_watermark=True`: denetlenmemiş kurum başlıklı satır kaldıysa `son_tarama`
      imlecini o satırın önüne çeker; eksik kontrolü tam kapsam gibi göstermez.
    """
    scan_started = _now_utc(clock)
    if not feed.items and not feed.rejected:
        return ScanReport(0, 0, {}, suspicious_empty=True)

    allowed = {t.strip().upper() for t in universe} if universe is not None else None
    code_to_oid: dict[str, str] = {}
    oid_groups: dict[str, list[str]] = {}
    for entry in companies:
        code = entry.ticker.strip().upper()
        if code and entry.oid:
            code_to_oid[code] = entry.oid
            group = oid_groups.setdefault(entry.oid, [])
            if code not in group:
                group.append(code)
    oid_to_codes: dict[str, tuple[str, ...]] = {oid: tuple(grp) for oid, grp in oid_groups.items()}
    counts: Counter[str] = Counter()
    refresh_tickers: list[str] = []
    earliest_incomplete: datetime | None = None
    detail_calls = 0
    probes_used = 0
    unprobed_opaque = 0

    # Eskiden yeniye işle ki aynı turdaki düzeltme hedefinden sonra yazılabilsin.
    ordered = sorted(feed.items, key=lambda s: (s.published_at, s.index))

    for stub in ordered:
        fp = _fingerprint(stub)
        cached = archive.seen_status(stub.index, fp, origin=origin)
        if cached in _TERMINAL_SEEN:
            counts["atlandi_ayni"] += 1
            continue

        triage = route(stub.disclosure_class, stub.subject, stub.summary)
        now = max(_now_utc(clock), stub.published_at.astimezone(timezone.utc))

        if triage is not Route.PIPELINE:
            status = {
                Route.ROUTINE: "filtered_routine",
                Route.MECHANICAL: "filtered_mechanical",
                Route.FUNDAMENTALS_REFRESH: "fundamentals_refresh",
            }[triage]
            if triage is Route.FUNDAMENTALS_REFRESH:
                tkr, _ = _resolve_single_ticker(stub, resolver)
                if tkr and (allowed is None or tkr in allowed):
                    refresh_tickers.append(tkr)
            retracted = archive.retract(
                stub.index, published_at=stub.published_at, known_at=now,
                reason=status, origin=origin, minute_precision=stub.minute_precision,
            )
            final = retracted if retracted == "geri_cekildi" else status
            counts[final] += 1
            archive.mark_seen(stub.index, fp, final, updated_at=now, origin=origin)
            continue

        if is_index_agenda(stub.title, stub.subject, stub.tickers):
            retracted = archive.retract(
                stub.index, published_at=stub.published_at, known_at=now,
                reason="agenda", origin=origin, minute_precision=stub.minute_precision,
            )
            final = retracted if retracted == "geri_cekildi" else "agenda"
            counts[final] += 1
            archive.mark_seen(stub.index, fp, final, updated_at=now, origin=origin)
            continue

        if not stub.tickers and is_non_equity_disclosure(stub.title, stub.subject, stub.summary):
            retracted = archive.retract(
                stub.index, published_at=stub.published_at, known_at=now,
                reason="filtered_non_equity", origin=origin, minute_precision=stub.minute_precision,
            )
            final = retracted if retracted == "geri_cekildi" else "filtered_non_equity"
            counts[final] += 1
            archive.mark_seen(stub.index, fp, final, updated_at=now, origin=origin)
            continue

        if is_low_signal_corporate(stub.subject):
            retracted = archive.retract(
                stub.index, published_at=stub.published_at, known_at=now,
                reason="filtered_low_signal", origin=origin, minute_precision=stub.minute_precision,
            )
            final = retracted if retracted == "geri_cekildi" else "filtered_low_signal"
            counts[final] += 1
            archive.mark_seen(stub.index, fp, final, updated_at=now, origin=origin)
            continue

        targets, is_multi = _resolve_single_ticker(
            stub, resolver, code_to_oid=code_to_oid, oid_to_codes=oid_to_codes, allowed=allowed,
        )
        if is_multi:
            retracted = archive.retract(
                stub.index, published_at=stub.published_at, known_at=now,
                reason="cok_hisseli", origin=origin, minute_precision=stub.minute_precision,
            )
            final = retracted if retracted == "geri_cekildi" else "cok_hisseli"
            counts[final] += 1
            archive.mark_seen(stub.index, fp, final, updated_at=now, origin=origin)
            continue

        detail: DisclosureDetail | None = None
        if not targets:
            # BIST dışı ihraççı OID'i kesin olarak biliniyorsa gereksiz detay denemesi yapma.
            if (
                stub.member_oid
                and oid_to_codes
                and stub.member_oid not in oid_to_codes
                and not stub.tickers
                and not _is_opaque_issuer(stub.title)
            ):
                counts["kapsam_disi"] += 1
                archive.mark_seen(stub.index, fp, "kapsam_disi", updated_at=now, origin=origin)
                continue
            opaque = _is_opaque_issuer(stub.title)
            should_probe = (
                detail_fetcher is not None
                and (probe_unresolved or (probe_opaque and opaque))
                and probes_used < max_unresolved_probes
            )
            if should_probe:
                assert detail_fetcher is not None
                probes_used += 1
                try:
                    detail_calls += 1
                    fetched = await detail_fetcher(stub.index)
                except Exception:
                    counts["detay_hatasi"] += 1
                    earliest_incomplete = _min_dt(earliest_incomplete, stub.published_at)
                    continue
                if not fetched.matches_record(stub):
                    counts["detay_uyusmazligi"] += 1
                    continue
                detail = fetched
                targets, is_multi = _resolve_single_ticker(
                    detail.stub, resolver, code_to_oid=code_to_oid,
                    oid_to_codes=oid_to_codes, allowed=allowed,
                )
                if is_multi:
                    det_known = max(_now_utc(clock), detail.stub.published_at.astimezone(timezone.utc))
                    retracted = archive.retract(
                        stub.index, published_at=detail.stub.published_at,
                        known_at=det_known, reason="cok_hisseli", origin=origin,
                    )
                    final = retracted if retracted == "geri_cekildi" else "cok_hisseli"
                    counts[final] += 1
                    archive.mark_seen(stub.index, fp, final, updated_at=det_known, origin=origin)
                    continue
                if not targets:
                    # Detay da BIST hissesi taşımıyorsa (genel kurum/borsa dışı ihraççı) tekrar indirme.
                    counts["kapsam_disi"] += 1
                    archive.mark_seen(stub.index, fp, "kapsam_disi", updated_at=now, origin=origin)
                    continue
            else:
                if opaque:
                    unprobed_opaque += 1
                    if strict_watermark:
                        earliest_incomplete = _min_dt(earliest_incomplete, stub.published_at)
                counts["unresolved_ticker"] += 1
                continue

        if allowed is not None:
            targets = tuple(t for t in targets if t in allowed)
            if not targets:
                counts["dis_evren"] += 1
                archive.mark_seen(stub.index, fp, "dis_evren", updated_at=now, origin=origin)
                continue

        if detail is None and detail_fetcher is not None:
            try:
                detail_calls += 1
                fetched = await detail_fetcher(stub.index)
            except Exception:
                pending_at = max(_now_utc(clock), stub.published_at.astimezone(timezone.utc))
                for t in targets:
                    archive.record_pending(
                        stub, ticker=t, known_at=pending_at,
                        reason="detay_bekleniyor", origin=origin,
                    )
                archive.mark_seen(stub.index, fp, "detay_bekleniyor", updated_at=pending_at, origin=origin)
                counts["detay_bekleniyor"] += 1
                earliest_incomplete = _min_dt(earliest_incomplete, stub.published_at)
                continue
            if not fetched.matches_record(stub):
                counts["detay_uyusmazligi"] += 1
                continue
            detail = fetched

        ticker_switched = False
        det_codes: tuple[str, ...] = ()
        if detail is not None:
            det_codes = tuple(dict.fromkeys(c.strip().upper() for c in detail.stub.tickers if c and c.strip()))
            det_oid = detail.stub.member_oid
            det_known = max(_now_utc(clock), detail.stub.published_at.astimezone(timezone.utc))
            if det_codes:
                resolved_det, det_multi = _collapse_company_codes(
                    det_codes, code_to_oid, allowed,
                    member_oid=det_oid if (det_oid and not _is_opaque_issuer(detail.stub.title)) else None,
                )
                if det_multi:
                    retracted = archive.retract(
                        stub.index, published_at=detail.stub.published_at,
                        known_at=det_known, reason="cok_hisseli", origin=origin,
                    )
                    final = retracted if retracted == "geri_cekildi" else "cok_hisseli"
                    counts[final] += 1
                    archive.mark_seen(stub.index, fp, final, updated_at=det_known, origin=origin)
                    continue
                if set(resolved_det) != set(targets):
                    ticker_switched = True
                    targets = resolved_det
            elif det_oid and oid_to_codes and not _is_opaque_issuer(detail.stub.title):
                oid_codes = oid_to_codes.get(det_oid)
                if oid_codes is None:
                    # Detaydaki ihraççı OID'i BIST şirket listesinde yok (ör. borsada işlem görmeyen ihraççı).
                    retracted = archive.retract(
                        stub.index, published_at=detail.stub.published_at,
                        known_at=det_known, reason="kapsam_disi", origin=origin,
                    )
                    final = retracted if retracted == "geri_cekildi" else "kapsam_disi"
                    counts[final] += 1
                    archive.mark_seen(stub.index, fp, final, updated_at=det_known, origin=origin)
                    continue
                resolved_oid, _ = _collapse_company_codes(oid_codes, code_to_oid, allowed, member_oid=det_oid)
                if resolved_oid and set(resolved_oid) != set(targets):
                    ticker_switched = True
                    targets = resolved_oid
                det_codes = resolved_oid

            if allowed is not None:
                in_allowed = tuple(t for t in targets if t in allowed)
                if not in_allowed:
                    retracted = archive.retract(
                        stub.index, published_at=detail.stub.published_at,
                        known_at=det_known, reason="dis_evren", origin=origin,
                    )
                    final = retracted if retracted == "geri_cekildi" else "dis_evren"
                    counts[final] += 1
                    archive.mark_seen(stub.index, fp, final, updated_at=det_known, origin=origin)
                    continue
                targets = in_allowed

            det_subj = detail.stub.subject or stub.subject
            det_summ = detail.stub.summary or stub.summary
            det_route = route(
                detail.stub.disclosure_class or stub.disclosure_class, det_subj, det_summ,
            )
            if det_route is not Route.PIPELINE or is_low_signal_corporate(det_subj):
                status = (
                    "filtered_low_signal"
                    if det_route is Route.PIPELINE
                    else {
                        Route.ROUTINE: "filtered_routine",
                        Route.MECHANICAL: "filtered_mechanical",
                        Route.FUNDAMENTALS_REFRESH: "fundamentals_refresh",
                    }[det_route]
                )
                if det_route is Route.FUNDAMENTALS_REFRESH:
                    refresh_tickers.extend(targets)
                retracted = archive.retract(
                    stub.index, published_at=detail.stub.published_at,
                    known_at=det_known, reason=status, origin=origin,
                )
                final = retracted if retracted == "geri_cekildi" else status
                counts[final] += 1
                archive.mark_seen(stub.index, fp, final, updated_at=det_known, origin=origin)
                continue

        # Aynı şirket içinde birden çok izlenen pay sınıfı varsa (ör. hem KRDMA hem KRDMD
        # izleniyorsa) tek şirketin bildirimi ayrı disclosure_id kısıtına takılmadan birincil
        # izlenen koda yazılır.
        ticker = select_primary_ticker(targets, allowed=allowed) or targets[0]
        effective_detail = _detail_for_ticker(detail, ticker) if detail is not None else None
        if detail is not None:
            det_subj = detail.stub.subject or stub.subject
            effective_stub = DisclosureStub(
                index=stub.index,
                published_at=detail.stub.published_at,
                title=detail.stub.title or stub.title,
                subject=det_subj,
                summary=stub.summary if stub.summary is not None else detail.stub.summary,
                disclosure_class=detail.stub.disclosure_class or stub.disclosure_class,
                tickers=(ticker,) if det_codes else ((ticker,) if stub.tickers else ()),
                member_oid=detail.stub.member_oid or stub.member_oid,
            )
        else:
            effective_stub = (
                stub if not stub.tickers or stub.tickers == (ticker,)
                else DisclosureStub(
                    index=stub.index,
                    published_at=stub.published_at,
                    title=stub.title,
                    subject=stub.subject,
                    summary=stub.summary,
                    disclosure_class=stub.disclosure_class,
                    tickers=(ticker,),
                    member_oid=stub.member_oid,
                    minute_precision=stub.minute_precision,
                )
            )

        clean_body = body_text(detail.body_html) if detail is not None else ""
        proposal: EvidenceProposal | None = None
        if reviewer is not None:
            try:
                proposal = await reviewer.propose(effective_stub, ticker, effective_detail, clean_body)
            except Exception:
                proposal = None
        if proposal is None:
            proposal = extract_verified_evidence(effective_stub, clean_body)

        ready_at = max(_now_utc(clock), effective_stub.published_at.astimezone(timezone.utc))
        if proposal is not None:
            candidate = make_candidate(effective_stub, ticker)
            outcome = archive.record(
                candidate,
                origin=origin,
                known_at=ready_at,
                topic=proposal.topic,
                quote=proposal.quote,
                detail=effective_detail,
                statements=statements.get(ticker, ()) if statements else (),
                valid_until=proposal.valid_until,
                supersedes=proposal.supersedes,
                reviewed=proposal.reviewed,
                defer_missing_target=proposal.supersedes is not None,
            )
            counts[outcome] += 1
            archive.mark_seen(stub.index, fp, outcome, updated_at=ready_at, origin=origin)
        else:
            if ticker_switched:
                archive.retract(
                    effective_stub.index, published_at=effective_stub.published_at,
                    known_at=ready_at, reason="yanlis_atif", origin=origin,
                )
            pending_reason = (
                "detay_bekleniyor" if detail is None
                else ("ihale_sonucu_icin_detay_gerekli"
                      if "ihale" in tr_lower(effective_stub.subject)
                      else "inceleme_bekleniyor")
            )
            outcome = archive.record_pending(
                effective_stub, ticker=ticker, known_at=ready_at,
                reason=pending_reason, origin=origin,
            )
            counts[outcome] += 1
            archive.mark_seen(stub.index, fp, outcome, updated_at=ready_at, origin=origin)

    observed_end = _now_utc(clock)
    scanned_until = (
        _compute_watermark(
            feed=feed,
            scan_started=scan_started,
            observed_end=observed_end,
            earliest_incomplete=earliest_incomplete,
        )
        if update_watermark
        else None
    )
    if scanned_until is not None:
        archive.record_scan(
            scanned_until,
            observed_at=max(observed_end, scanned_until),
            origin=origin,
        )

    return ScanReport(
        total_rows=len(feed.items),
        rejected_rows=len(feed.rejected),
        counts=dict(counts),
        detail_requests=detail_calls,
        unprobed_opaque=unprobed_opaque,
        fundamentals_refresh=tuple(dict.fromkeys(refresh_tickers)),
        scanned_until=scanned_until,
        suspicious_empty=False,
    )


def _min_dt(current: datetime | None, candidate: datetime) -> datetime:
    utc = candidate.astimezone(timezone.utc)
    return utc if current is None or utc < current else current


def _compute_watermark(
    *,
    feed: ParseResult,
    scan_started: datetime,
    observed_end: datetime,
    earliest_incomplete: datetime | None,
) -> datetime | None:
    if feed.rejected or not feed.items:
        return None
    latest_pub = max(s.published_at.astimezone(timezone.utc) for s in feed.items)
    covered = min(max(scan_started, latest_pub), observed_end)
    if earliest_incomplete is not None:
        before_gap = earliest_incomplete - timedelta(microseconds=1)
        earliest_pub = min(s.published_at.astimezone(timezone.utc) for s in feed.items)
        if before_gap < earliest_pub:
            return None
        covered = min(covered, before_gap)
    return covered


async def sync_once(
    kap: KapClient,
    archive: AlarmArchive,
    *,
    resolver: TickerResolver,
    reviewer: EvidenceReviewer | None = None,
    statements: Mapping[str, Sequence[PublishedRow]] | None = None,
    universe: Sequence[str] | None = None,
    companies: Sequence[CompanyEntry] = (),
    origin: Origin = "kap_arsiv",
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    probe_unresolved: bool = False,
    probe_opaque: bool = False,
    max_unresolved_probes: int = 25,
    strict_watermark: bool = True,
) -> ScanReport:
    """Canlı KAP listesinden tek tur sessiz toplama yapar (arka plan zamanlayıcısı birimi)."""
    feed = await kap.light()
    return await ingest_feed(
        feed,
        archive,
        resolver=resolver,
        detail_fetcher=kap.detail,
        reviewer=reviewer,
        statements=statements,
        universe=universe,
        companies=companies,
        origin=origin,
        clock=clock,
        probe_unresolved=probe_unresolved,
        probe_opaque=probe_opaque,
        max_unresolved_probes=max_unresolved_probes,
        strict_watermark=strict_watermark,
    )


class KapCollectorService:
    """Tek uygulama / tek Render dağıtımı için arka plan sessiz KAP toplayıcısı.

    - Şirket unvan/OID listesini SQLite içinde önbellekler (`company_ttl`, varsayılan 24 saat).
    - KAP şirket listesi geçici hata verirse bayat önbellekle taramayı sürdürür.
    - `warmup()` ile soğuk başlangıç / restart sonrası izlenen hisselerin son `max_age`
      penceresindeki bildirimlerini gerçek gözlem saatiyle (`known_at`) arşive alır.
    - Eski imleç/tarama/sürüm satırlarını günde bir budar.
    """

    def __init__(
        self,
        archive_path: str | Path,
        *,
        universe: Sequence[str] | None = None,
        reviewer: EvidenceReviewer | None = None,
        statements: Mapping[str, Sequence[PublishedRow]] | None = None,
        kap: KapClient | None = None,
        company_ttl: timedelta = timedelta(hours=24),
        probe_opaque: bool = True,
        probe_unresolved: bool = False,
        max_unresolved_probes: int = 30,
        seen_retention_days: int = 14,
        version_retention_days: int | None = 30,
        origin: Origin = "kap_arsiv",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.archive_path = Path(archive_path)
        self.universe = tuple(dict.fromkeys(t.strip().upper() for t in universe if t.strip())) if universe else None
        self.reviewer = reviewer
        self.statements = statements
        self._external_kap = kap
        self.company_ttl = company_ttl
        self.probe_opaque = probe_opaque
        self.probe_unresolved = probe_unresolved
        self.max_unresolved_probes = max_unresolved_probes
        self.seen_retention_days = seen_retention_days
        self.version_retention_days = version_retention_days
        self.origin: Origin = origin
        self.clock = clock
        self._sleep = sleep
        self._log = logger or logging.getLogger(__name__)
        self._last_prune: datetime | None = None

    async def _ensure_companies(self, kap: KapClient, archive: AlarmArchive) -> list[CompanyEntry]:
        now = _now_utc(self.clock)
        fresh = archive.load_companies(as_of=now, max_age=self.company_ttl)
        if fresh:
            return fresh
        try:
            fetched = await fetch_companies(kap)
        except (KapError, CompanyMapError, OSError) as exc:
            stale = archive.load_companies(as_of=now, max_age=None)
            if stale:
                self._log.warning("KAP şirket listesi yenilenemedi, önbellek kullanılıyor: %s", exc)
                return stale
            raise
        archive.save_companies(fetched, updated_at=now)
        return fetched

    async def warmup(
        self,
        tickers: Sequence[str] | None = None,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        max_details_per_ticker: int = 10,
    ) -> ScanReport:
        """Restart sonrası izlenen hisselerin son `max_age` penceresindeki geçmişini doldurur."""
        targets = tuple(
            dict.fromkeys(t.strip().upper() for t in (tickers if tickers is not None else (self.universe or ())) if t.strip())
        )
        if not targets:
            return ScanReport(0, 0, {})

        async def _run(kap: KapClient) -> ScanReport:
            with open_archive(self.archive_path, writable=True) as archive:
                companies = await self._ensure_companies(kap, archive)
                oid_map = {c.ticker: c.oid for c in companies}
                resolver = build_company_resolver(companies, preferred=targets)
                now = _now_utc(self.clock)
                cutoff = now - max_age
                skew_ceiling = now + timedelta(seconds=60)
                today_trt = to_trt(now).date()
                stubs: list[DisclosureStub] = []
                seen_oids: set[str] = set()
                for ticker in targets:
                    oid = oid_map.get(ticker)
                    if not oid or oid in seen_oids:
                        continue
                    seen_oids.add(oid)
                    rows = await kap.fetch_history(oid, today=today_trt)
                    picked = 0
                    for row in rows:
                        row_pub = (
                            row.published_at.replace(tzinfo=TRT).astimezone(timezone.utc)
                            if row.published_at.utcoffset() is None
                            else row.published_at.astimezone(timezone.utc)
                        )
                        if row_pub < cutoff or row_pub > skew_ceiling:
                            continue
                        if route(row.disclosure_class, row.subject, row.summary) is not Route.PIPELINE:
                            continue
                        if is_low_signal_corporate(row.subject):
                            continue
                        stubs.append(DisclosureStub(
                            index=row.index,
                            published_at=row_pub,
                            title=ticker,
                            subject=row.subject,
                            summary=row.summary,
                            disclosure_class=row.disclosure_class,
                            tickers=(ticker,),
                            member_oid=oid,
                            minute_precision=True,
                        ))
                        picked += 1
                        if picked >= max_details_per_ticker:
                            break
                if not stubs:
                    return ScanReport(0, 0, {})
                return await ingest_feed(
                    ParseResult(stubs),
                    archive,
                    resolver=resolver,
                    detail_fetcher=kap.detail,
                    reviewer=self.reviewer,
                    statements=self.statements,
                    universe=targets,
                    companies=companies,
                    origin=self.origin,
                    clock=self.clock,
                    update_watermark=False,
                )

        if self._external_kap is not None:
            return await _run(self._external_kap)
        async with KapClient() as kap:
            return await _run(kap)

    async def run_once(self) -> ScanReport:
        """Şirket önbelleğini kontrol eder, tek tur tarama yapar ve gerekirse budar."""
        async def _run(kap: KapClient) -> ScanReport:
            with open_archive(self.archive_path, writable=True) as archive:
                companies = await self._ensure_companies(kap, archive)
                resolver = build_company_resolver(companies, preferred=self.universe)
                report = await sync_once(
                    kap,
                    archive,
                    resolver=resolver,
                    reviewer=self.reviewer,
                    statements=self.statements,
                    universe=self.universe,
                    companies=companies,
                    origin=self.origin,
                    clock=self.clock,
                    probe_unresolved=self.probe_unresolved,
                    probe_opaque=self.probe_opaque,
                    max_unresolved_probes=self.max_unresolved_probes,
                    strict_watermark=True,
                )
                now = _now_utc(self.clock)
                if self._last_prune is None or now - self._last_prune >= timedelta(hours=24):
                    archive.prune(
                        now,
                        seen_days=self.seen_retention_days,
                        version_days=self.version_retention_days,
                        origin=self.origin,
                    )
                    self._last_prune = now
                return report

        if self._external_kap is not None:
            return await _run(self._external_kap)
        async with KapClient() as kap:
            return await _run(kap)

    async def run_forever(
        self,
        *,
        interval_seconds: float = 180.0,
        warmup_on_start: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Ana uygulama içinde `asyncio.create_task` ile çalıştırılacak dayanıklı döngü."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds pozitif olmalı")

        async def _loop(kap: KapClient) -> None:
            bound = KapCollectorService(
                self.archive_path,
                universe=self.universe,
                reviewer=self.reviewer,
                statements=self.statements,
                kap=kap,
                company_ttl=self.company_ttl,
                probe_opaque=self.probe_opaque,
                probe_unresolved=self.probe_unresolved,
                max_unresolved_probes=self.max_unresolved_probes,
                seen_retention_days=self.seen_retention_days,
                version_retention_days=self.version_retention_days,
                origin=self.origin,
                clock=self.clock,
                sleep=self._sleep,
                logger=self._log,
            )
            if warmup_on_start and self.universe:
                need_warmup = True
                if self.archive_path.exists():
                    with open_archive(self.archive_path) as reader:
                        need_warmup = reader.last_scan(origin=self.origin) is None
                if need_warmup:
                    try:
                        await bound.warmup(self.universe)
                    except Exception as exc:
                        self._log.warning("KAP başlangıç ısınması tamamlanamadı: %s", exc)

            while stop_event is None or not stop_event.is_set():
                try:
                    report = await bound.run_once()
                    if report.suspicious_empty:
                        self._log.warning("KAP /list/light boş döndü; tarama damgası ilerletilmedi")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log.warning("KAP tarama turu hatası (sonraki turda denenecek): %s", exc)
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                        break
                    except TimeoutError:
                        continue
                await self._sleep(interval_seconds)

        if self._external_kap is not None:
            await _loop(self._external_kap)
            return
        async with KapClient() as kap:
            await _loop(kap)
