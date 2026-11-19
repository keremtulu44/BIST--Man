"""Bağlam paketi: incelenmiş **tek** olaydan sağlayıcı bağımsız kanıt paketi.

Konu ve belirleyici alıntı kayıtta doğrulanır; kısaltma ortasından kesilmiş
parçalar, karşı taraf unvanına takılmadan şirket satışı olmayan sözleşmeler ve
olumsuzlanmış/sona ermiş üretim duruşları bekletilir. Eski skor, bugünkü bilanço
ve önceki haberler pakete kopyalanmaz. Ağ/LLM çağrısı yok.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from .fundamentals import PublishedRow, statement_at
from .parsing.evidence import (
    event_amount,
    issuer_signature,
    sale_qualified,
    stoppage_confirmed,
    stoppage_contradicted,
)
from .parsing.kap_table import body_text
from .parsing.money import find_amounts
from .parsing.text import is_abbreviation_break
from .sources.base import DisclosureDetail, DisclosureStub
from .triage import Route, is_index_agenda, route

Topic = Literal["satis_sozlesmesi", "uretim_durusu", "sirket_aciklamasi", "endeks_parametresi"]
Origin = Literal["kap_arsiv", "sentetik", "dogrulanmamis"]
MAX_CHARS = 1000  # Kompakt JSON'un Unicode karakterleri; token veya API maliyeti değil.
IMPLAUSIBLE_RATIO_PCT = 100.0
UNRELIABLE_TTM = frozenset({
    "ttm_gap", "data_gap", "unit_mismatch", "stale_fundamentals",
    "mixed_restatement_vintage", "assumed_mode", "bank_template_mismatch",
})


def _revenue_ratio_pct(amount_try: Decimal, revenue_ttm: Decimal) -> float | None:
    if amount_try <= 0 or revenue_ttm <= 0:
        return None
    try:
        pct = float((amount_try / revenue_ttm) * Decimal(100))
    except (InvalidOperation, OverflowError, ZeroDivisionError):
        return None
    return pct if math.isfinite(pct) and pct > 0 else None


class DisclosureItem(Protocol):
    """Paketleyici ve arşivin ihtiyaç duyduğu dar sözleşme (skor motoru şart değil)."""

    @property
    def disclosure(self) -> DisclosureStub: ...
    @property
    def route(self) -> Route: ...
    @property
    def status(self) -> str: ...
    @property
    def ticker(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ArchiveCandidate:
    """Eski skor/bildirim hattına girmeden arşive yazılabilen yalın aday."""

    disclosure: DisclosureStub
    route: Route
    status: str
    ticker: str | None = None


def make_candidate(disclosure: DisclosureStub, ticker: str | None = None) -> ArchiveCandidate:
    """Ham bildirim + çözülmüş hisseden skorsuz `ArchiveCandidate` üretir."""
    r = route(disclosure.disclosure_class, disclosure.subject, disclosure.summary)
    if r is not Route.PIPELINE:
        status = {
            Route.ROUTINE: "filtered_routine",
            Route.MECHANICAL: "filtered_mechanical",
            Route.FUNDAMENTALS_REFRESH: "fundamentals_refresh",
        }[r]
        return ArchiveCandidate(disclosure, r, status, None)
    if is_index_agenda(disclosure.title, disclosure.subject, disclosure.tickers):
        return ArchiveCandidate(disclosure, r, "agenda", None)
    code = (ticker or "").strip().upper() or None
    if code is None:
        return ArchiveCandidate(disclosure, r, "unresolved_ticker", None)
    return ArchiveCandidate(disclosure, r, "no_fundamentals", code)


class PacketHeld(ValueError):
    """Eksik kimlik/kanıt: bu kayıt API'ye hazır değil. Mesajı denetim nedenidir."""


def compact_json(packet: dict[str, object]) -> str:
    return json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _sale_context(
    quote: str, record: Sequence[str], ticker: str, at: datetime, rows: Sequence[PublishedRow],
) -> dict[str, object]:
    amounts = find_amounts(quote)
    if len(amounts) != 1:
        # Konsorsiyum toplamı, şirket payı, eski/yeni bedel: en büyüğünü seçme.
        return {"sinir": "ana_tutar_belirsiz" if amounts else "tutar_verilmedi"}
    amount = event_amount(quote, contract=True).money
    if amount is None:
        return {"sinir": "ana_tutar_belirsiz"}
    full = event_amount(*record, contract=True).money
    if full is None or (full.amount, full.currency) != (amount.amount, amount.currency):
        raise PacketHeld("bedel_baglamla_celisiyor")
    if any(w.startswith("belirsiz_virgul_binlik:") for w in amount.warnings):
        return {"sinir": "tutar_bicimi_belirsiz"}
    fields: dict[str, object] = {"tutar": {"deger": str(amount.amount), "birim": amount.currency}}
    if amount.currency != "TRY":
        return {**fields, "sinir": "para_birimi_belirsiz" if amount.currency == "UNKNOWN" else "tarihli_kur_yok"}

    version = statement_at(rows, ticker, at)
    if version is None:
        return {**fields, "sinir": "haber_aninda_yayimlanmis_tablo_yok"}
    row = version.row
    revenue = row.revenue_ttm
    if (
        row.currency != "TRY" or row.statement_type != "industrial" or not row.source
        or revenue is None or not math.isfinite(revenue) or revenue <= 0
        or UNRELIABLE_TTM.intersection(row.flags)
    ):
        return {**fields, "sinir": "karsilastirilabilir_ttm_ciro_yok"}
    ratio = _revenue_ratio_pct(amount.amount, Decimal(str(revenue)))
    assert version.published_at is not None
    if ratio is None:
        return {**fields, "sinir": "oran_hesaplanamadi"}
    rounded_pct = round(ratio, 2) if ratio >= 0.005 else round(ratio, 6)
    fields["olcek"] = {
        "ttm_ciro_try": row.revenue_ttm, "bedel_ciro_yuzde": rounded_pct,
        "donem_sonu": row.period_end, "tablo_yayini": version.published_at.isoformat(),
        "kaynak": row.source,
    }
    if ratio > IMPLAUSIBLE_RATIO_PCT:
        return {**fields, "sinir": "oran_dogrulanmali"}
    return fields


def _starts_with_lowercase_word(quote: str) -> bool:
    stripped = quote.lstrip()
    return bool(stripped) and stripped[0].isalpha() and stripped[0].islower()


def _whole_excerpt(text: str, quote: str) -> bool:
    # Tüm alan (ör. noktasız KAP başlığı) ya da gerçek cümle/paragraf sınırları
    # arasında bir alıntı. O(1) sınır penceresiyle büyük gövdelerde karesel tarama yapmaz.
    # - '... yaptırım kararı bulunmamaktadır' içinden yalnız 'yaptırım kararı' alınamaz.
    # - 'İddiaların aksine Prof. Dr. Ahmet ...' içinden 'Ahmet ...' alınamaz.
    # - 'Örnek A.Ş. hakkında ceza ...' içinden 'hakkında ceza ...' alınamaz.
    # - 'Açıklamalar\nSon günlerde ...' gibi başlık sonrası paragraf başı geçerlidir.
    if text.strip() == quote:
        return True
    if not quote or not quote.endswith((".", "!", "?")) or _starts_with_lowercase_word(quote):
        return False
    qlen = len(quote)
    tlen = len(text)
    pos = text.find(quote)
    while pos != -1:
        end = pos + qlen
        if end == tlen or text[end].isspace():
            # Sağ sınır: alıntı bir kısaltma noktasıyla aynı satırda kesilmemiş olmalı.
            ws_end = end
            after_nl = False
            while ws_end < tlen and text[ws_end].isspace():
                if text[ws_end] == "\n":
                    after_nl = True
                    break
                ws_end += 1
            after_ok = (
                end == tlen
                or after_nl
                or not is_abbreviation_break(quote, text[end:min(tlen, ws_end + 64)])
            )
            if after_ok:
                # Sol sınır: metin/paragraf başı ya da kısaltma olmayan önceki cümle sonu.
                b_idx = pos
                before_nl = False
                while b_idx > 0 and text[b_idx - 1].isspace():
                    if text[b_idx - 1] == "\n":
                        before_nl = True
                    b_idx -= 1
                starts_paragraph = b_idx == 0 or before_nl
                starts_sentence = (
                    not starts_paragraph
                    and b_idx < pos
                    and text[b_idx - 1] in ".!?"
                    and not is_abbreviation_break(
                        text[max(0, b_idx - 128):pos],
                        text[pos:min(tlen, pos + 64)],
                    )
                )
                if starts_paragraph or starts_sentence:
                    return True
        pos = text.find(quote, pos + 1)
    return False


def build_packet(
    item: DisclosureItem, *, origin: Origin, topic: Topic, quote: str,
    detail: DisclosureDetail | None = None, statements: Sequence[PublishedRow] = (),
) -> dict[str, object]:
    """KAP kaydı veya açıkça sentetik girdiden tek paket; yetersizse `PacketHeld`.

    `origin` arşivi yükleyen tarafın bilgisidir; URL'den güven çıkarılmaz.
    `detail` kimliği denetlendikten sonra TR metnine çevrilir. Ana tutar yalnız seçilen
    satış sözleşmesi alıntısından okunur, başka paragraflardan taşınmaz.
    """
    d = item.disclosure
    if origin not in {"kap_arsiv", "sentetik"}:
        raise PacketHeld("kaynak_dogrulanmadi")
    if d.index <= 0 or d.published_at.utcoffset() is None:
        raise PacketHeld("kimlik_veya_yayin_zamani_eksik")

    ref = (f"https://www.kap.org.tr/tr/Bildirim/{d.index}" if origin == "kap_arsiv"
           else f"senaryo:{d.index}")
    published_at = d.published_at
    packet: dict[str, object] = {
        "konu": topic, "kaynak": ref, "yayin": published_at.astimezone(timezone.utc).isoformat(),
    }
    if topic == "endeks_parametresi":
        # Pilotun doğrulanmış dar piyasa sınıfı. Hissesiz her KAP/BİST kaydı
        # piyasa duyurusu değildir (ör. tek payda hak kullanımı/devre kesici).
        if item.ticker or not is_index_agenda(d.title, d.subject, d.tickers):
            raise PacketHeld("piyasa_kapsami_dogrulanmadi")
        packet.update(kapsam="piyasa_gundemi", kurum=d.title)
    elif topic in {"satis_sozlesmesi", "uretim_durusu", "sirket_aciklamasi"}:
        if item.status not in {"scored", "no_fundamentals"} or not item.ticker:
            raise PacketHeld(item.status)
        packet.update(kapsam="hisse", hisse=item.ticker)
    else:
        raise PacketHeld("konu_kapsam_disi")

    body = ""
    if detail is not None:
        if not detail.matches(d, item.ticker):
            raise PacketHeld("detay_kayit_uyusmazligi")
        body = body_text(detail.body_html)
        if d.minute_precision and not detail.stub.minute_precision:
            published_at = detail.stub.published_at
            packet["yayin"] = published_at.astimezone(timezone.utc).isoformat()

    if quote.strip() in {"", "-", "—"}:
        raise PacketHeld("olay_kaniti_eksik")
    sources = (("govde", body), ("ozet", d.summary), ("baslik", d.subject))
    matches = [(loc, text) for loc, text in sources if text and quote in text]
    if not matches:
        raise PacketHeld("alinti_kayitta_yok")
    location = next((loc for loc, text in matches if _whole_excerpt(text, quote)), None)
    if location is None:
        raise PacketHeld("alinti_cumle_sinirinda_degil")
    if d.subject == "İhale Süreci / Sonucu" and location != "govde":
        # Pilot kuralı: ihale sürecinin duyurulması, satışın kesinleşmesi değildir.
        raise PacketHeld("ihale_sonucu_icin_detay_gerekli")
    packet.update(kanit_yeri=location, kanit=quote)
    if topic == "satis_sozlesmesi":
        if issuer_signature(quote) != "sale" or sale_qualified(d.subject, d.summary or "", body):
            raise PacketHeld("satis_kaniti_yetersiz")
        assert item.ticker is not None
        packet.update(_sale_context(quote, (d.subject, d.summary or "", body), item.ticker, published_at, statements))
    elif topic == "uretim_durusu":
        if not stoppage_confirmed(quote) or stoppage_contradicted(d.subject, d.summary or "", body):
            raise PacketHeld("durus_kaniti_yetersiz")
    if len(compact_json(packet)) > MAX_CHARS:
        # Alıntının sonundaki bir olumsuzluğu/koşulu sessizce kesmemek için.
        raise PacketHeld("daha_kisa_tam_kanit_sec")
    return packet
