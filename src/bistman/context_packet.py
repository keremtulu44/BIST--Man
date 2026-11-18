"""Bağlam pilotu: incelenmiş **tek** olaydan sağlayıcı bağımsız kanıt paketi.

Bu bir konu sınıflandırıcısı veya gönderici değil. Konu ve belirleyici alıntı
pilot için etiketlenir; alıntının kayıtta olduğu kontrol edilir. Eski skor,
bugünkü bilanço ve önceki haberler pakete kopyalanmaz. Ağ/LLM çağrısı yok.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from .features import build_features
from .fundamentals import PublishedRow, statement_at
from .parsing.evidence import event_amount, issuer_signature, sale_qualified
from .parsing.kap_table import body_text
from .parsing.money import find_amounts
from .pipeline import ProcessedDisclosure
from .sources.base import DisclosureDetail
from .triage import is_index_agenda

Topic = Literal["satis_sozlesmesi", "uretim_durusu", "sirket_aciklamasi", "endeks_parametresi"]
Origin = Literal["kap_arsiv", "sentetik", "dogrulanmamis"]
MAX_CHARS = 1000  # Kompakt JSON'un Unicode karakterleri; token veya API maliyeti değil.
UNRELIABLE_TTM = frozenset({
    "ttm_gap", "stale_fundamentals", "mixed_restatement_vintage",
    "assumed_mode", "bank_template_mismatch",
})


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
    features = build_features(
        ticker=ticker, amount_try=amount.amount, revenue_ttm=Decimal(str(revenue))
    )
    assert version.published_at is not None
    if features.ratio_revenue is None:
        return {**fields, "sinir": "oran_hesaplanamadi"}
    fields["olcek"] = {
        "ttm_ciro_try": row.revenue_ttm, "bedel_ciro_yuzde": round(features.ratio_revenue, 2),
        "donem_sonu": row.period_end, "tablo_yayini": version.published_at.isoformat(),
        "kaynak": row.source,
    }
    if "implausible_ratio" in features.flags:
        return {**fields, "sinir": "oran_dogrulanmali"}
    return fields


def _whole_excerpt(text: str, quote: str) -> bool:
    # Tüm alan (ör. noktasız KAP başlığı) ya da cümle sınırları arasında bir alıntı.
    # '... yaptırım kararı bulunmamaktadır' içinden yalnız 'yaptırım kararı' alınamaz.
    if text.strip() == quote:
        return True
    for match in re.finditer(re.escape(quote), text):
        before, after = text[:match.start()], text[match.end():]
        starts_sentence = not before.strip() or (
            before[-1].isspace() and before.rstrip().endswith((".", "!", "?"))
        )
        if starts_sentence and quote.endswith((".", "!", "?")) and (not after or after[0].isspace()):
            return True
    return False


def build_packet(
    item: ProcessedDisclosure, *, origin: Origin, topic: Topic, quote: str,
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
    packet: dict[str, object] = {
        "konu": topic, "kaynak": ref, "yayin": d.published_at.astimezone(timezone.utc).isoformat(),
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
        packet.update(_sale_context(quote, (d.subject, d.summary or "", body), item.ticker, d.published_at, statements))
    if len(compact_json(packet)) > MAX_CHARS:
        # Alıntının sonundaki bir olumsuzluğu/koşulu sessizce kesmemek için.
        raise PacketHeld("daha_kisa_tam_kanit_sec")
    return packet
