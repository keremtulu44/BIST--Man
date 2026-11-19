"""Olaya ait bedel ve anlam sınırları: sayıyı/cümleyi bulmak anlamı kanıtlamaz."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .money import Money, find_amounts
from .text import split_clauses, tr_lower

_OTHER_BASIS = re.compile(
    r"\b(?:proje(?:nin)?|konsorsiyum(?:un)?)\s+(?:toplam\s+)?(?:bütçe|bedel|tutar)"
    r"|\b(?:pay|hisse) başına\b"
)
_CONTRACT_BACKGROUND = re.compile(
    r"\b(?:ödenmiş sermaye|sermayemiz|ciromuz|geçici teminat|kesin teminat)\w*"
    r"|\b(?:geçen\s+yıl|bir\s+önceki\s+yıl|önceki\s+hesap\s+dönem\w*)\b"
)
_OWN_SHARE = re.compile(r"\b(?:şirketimiz(?:e düşen pay|in payı)|payımıza düşen|payımız\b)")
_KDV_NOTE = r"(?:(?:\(\s*)?(?:\+\s*)?kdv(?:\s+(?:hariç|dahil))?(?:\s*\))?)"
_PRICE_BEFORE = re.compile(
    rf"\b(?:(?:sözleşme(?:nin)?|satış|sipariş(?:in)?|ihale(?:nin)?|iş(?:in)?)\s+(?:toplam\s+)?(?:bedeli|tutarı)"
    rf"|şirketimize düşen pay|şirketimizin payı|payımıza düşen (?:bedel|tutar))"
    rf"\s*[:=]?\s*(?:toplam\s+)?(?:{_KDV_NOTE}\s*)?$"
)
_DESC_TOKEN = r"[^\W\d_]+(?:[-/][^\W\d_]+)?"
_PRICE_AFTER = re.compile(
    rf"^\s*(?:{_KDV_NOTE}\s*)?(?:bedelli|tutarında|tutarındaki|değerinde|değerindeki)\s+"
    rf"(?:(?:{_DESC_TOKEN}|ve)\s+){{0,6}}"
    r"(?:sözleşme\w*|sipariş\w*|ihale\w*|işi?\b)",
    re.UNICODE,
)
_ISSUER_SUBJECT = re.compile(
    r"(?:^\s*(?:(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}\s+tarihinde|bugün(?:\s+itibarıyla)?|dün)\s*,?\s*)?"
    r"şirketimiz(?:ce|in|le)?(?:\s*[,:]|\s+))"
    r"|\b(?:şirketimiz(?:ce|le)?\s+(?:ile\s+.+?\s+arasında|tarafından|arasında))\b"
    r"|\buhdemizde\s+kalan\b"
)
_THIRD_PARTY_ONLY = re.compile(
    r"^\s*(?:başka\s+bir\s+şirket|rakip\s+şirket|üçüncü\s+kişi)"
)
_NON_SALE_CONTRACT = re.compile(
    r"\b(?:alım|satın\s+alma|kira|kiralama|kredi|finansman|refinansman|teminat|ipotek|"
    r"gizlilik|danışmanlık|sigorta|lisans\s+alım|abonelik)\s+sözleşme"
)
_SIGNATURE = re.compile(
    r"\b(?P<sale>"
    r"(?:(?:[^\W\d_]+\s+){0,3})?satış\s+sözleşme(?:si(?:ni)?|yi)?"
    r"|ihracat\s+sözleşme(?:si(?:ni)?|yi)?"
    r"|(?:anahtar\s+teslim\s+)?yapım\s+işi\s+sözleşme(?:si(?:ni)?|yi)?"
    r"|taahhüt\s+sözleşme(?:si(?:ni)?|yi)?"
    r"|anahtar\s+teslim\s+(?:[^\W\d_]+\s+){0,3}sözleşme(?:si(?:ni)?|yi)?"
    r")\s+(?:(?:\w+\s+){0,8})?imza(?:landı|lanmıştır|ladı|ladık|lamıştır)\b"
    r"|\b(?P<order>"
    r"(?:yeni\s+)?(?:(?:[^\W\d_]+\s+){0,3})?sipariş(?:i(?:ni)?)?"
    r"|(?:(?:[^\W\d_]+\s+){0,2})(?:teslimat\w*|üretim\w*|imalat\w*|kurulum\w*|montaj\w*|"
    r"yapım\w*|inşaat\w*|mühendislik|modernizasyon|elektrifikasyon|yazılım)\s+işi"
    r")\s+(?:alındı|alınmıştır|aldı|aldık|almıştır)\b"
    r"|\b(?P<contract>(?:tedarik\s+|iş\s+|hizmet\s+|yapım\s+)?sözleşme(?:si(?:ni)?|yi)?)\s+"
    r"(?:(?:\w+\s+){0,6})?imza(?:landı|lanmıştır|ladı|ladık|lamıştır)\b",
    re.UNICODE,
)
ContractKind = Literal["sale", "contract"]

# Olumsuzlanmış fesih/iptal ifadeleri ("feshedilmemiştir", "iptal edilmedi")
# geçerli bir sözleşmeyi yanlışlıkla iptal edilmiş saymasın.
_NEGATED_QUALIFICATION = re.compile(
    r"\b(?:feshedilme(?:di|miştir|mektedir|yecek\w*)|fesh\s*edilme(?:di|miştir)"
    r"|iptal\s+edilme(?:di|miştir|mektedir|yecek\w*)"
    r"|(?:fesih|iptal)\s+(?:edilmesi\s+)?söz\s+konusu\s+değil\w*)\b"
)
# Dar koruma: geçmiş bir imzayı anlatan cümleden sonraki feshi/koşulu koparma.
# Eşleşmemesi, hukukî kesinlik garantisi değildir.
_QUALIFICATION = re.compile(
    r"\b(?:iptal|fesih|fesh|feshet|feshed|imzalanmadı|imzalanmamış|imzalamadı"
    r"|alınmadı|alınmamış|almadı|yalanla|iddia)"
    r"|\b(?:ön\s+protokol|niyet\s+mektubu|mutabakat\s+zaptı)\b"
    r"|gerçeği\s+yansıtm|bağlayıcı\s+(?:değil|olma)|yürürlüğe\s+girmemiş|askıya\s+alın"
    r"|(?:iznine|onayına|koşuluna|şartına|opsiyona|opsiyonuna)\s+(?:bağlı|tabi)"
    r"|\bopsiyon\s+kapsamında\b|\bön\s+(?:koşul|şart)"
)

# Üretim duruşu: olumlu duruş beyanı ve bunu çürüten olumsuzluk/yeniden başlama.
_STOPPAGE_AFFIRMATIVE = re.compile(
    r"\b(?:üretim\w*|faaliyet\w*)\s+(?:(?:\w+\s+){0,3})?"
    r"(?:ara\s+veril(?:di|miştir|mektedir|ecek|ecektir)"
    r"|durdurul(?:du|muştur|maktadır|acak|acaktır)|dur(?:du|muştur))\b"
    r"|\b(?:üretim\s+|faaliyet\s+|hat\w*\s+|tesis\w*\s+)?duruş\w*\s+"
    r"(?:yaşan(?:dı|mıştır|maktadır)|meydana\s+gel(?:di|miştir)|gerçekleş(?:ti|miştir)|başla(?:dı|mıştır))\b"
)
_STOPPAGE_CONTRADICTION = re.compile(
    r"\b(?:ara\s+verilme(?:di|miştir|mektedir|yecek\w*)"
    r"|durdurulma(?:dı|mıştır|maktadır|yacak\w*)|durma(?:dı|mıştır)"
    r"|ara\s+verilmesi\s+söz\s+konusu\s+değil\w*)\b"
    r"|\bduruş\w*\s+(?:(?:\w+\s+){0,2})?(?:yaşanma(?:dı|mıştır|maktadır)"
    r"|gerçekleşme(?:di|miştir|mektedir)|meydana\s+gelme(?:di|miştir)"
    r"|bulunmama\w*|yok(?:tur)?|olma(?:dı|mıştır)|söz\s+konusu\s+değil\w*)\b"
    r"|\b(?:üretim\w*|faaliyet\w*)\s+(?:kesintisiz\s+|normal\s+(?:şekilde\s+|seyrinde\s+)?"
    r"|planlandığı\s+(?:şekilde\s+|gibi\s+)?)?devam\s+etmekte"
    r"|\b(?:üretim\w*|faaliyet\w*)\s+(?:yeniden|tekrar)\s+başla(?:dı|mıştır|tıldı|tılmıştır|nıldı|nılmıştır)\b"
    r"|\b(?:yeniden|tekrar)\s+(?:üretim\w*\s+)?başla(?:dı|mıştır|tıldı|tılmıştır|nıldı|nılmıştır)\b"
    r"|\bduruş\w*\s+sona\s+er(?:di|miştir)\b|\b(?:yeniden|tekrar)\s+devreye\s+alın(?:dı|mıştır)\b"
    r"|gerçeği\s+yansıtm|\byalanla|\biddia\w*\s+asılsız"
)

# Açık yokluk/ret içeren alıntının, hiç olumsuzluk taşımayan bir editör okumasıyla
# tersine çevrilmesini önleyen koruma ("yaptırım bulunmamaktadır" -> "ceza verildi").
_SOURCE_DENIAL = re.compile(
    r"\b(?:bulunmamaktadır|yoktur|söz\s+konusu\s+değildir|gerçeği\s+yansıtmamaktadır"
    r"|uygulanmamıştır|verilmemiştir|açılmamıştır|yaşanmamıştır|gerçekleşmemiştir"
    r"|kesilmemiştir|alınmamıştır|edilmemiştir|yapılmamıştır|asılsızdır)\b"
)
_READING_NEGATION = re.compile(
    r"\b(?:değil\w*|yok(?:tur)?|bulunma\w*|reddet\w*|yalanla\w*|asılsız\w*|olmaksızın|olmadığ\w*|olmayan)\b"
    r"|\w+m[ae](?:dı|di|du|dü|mış|miş|muş|müş|dığ|diğ|duğ|düğ|makta|mekte|yacak|yecek)\w*"
    r"|\w+m[ıiuü]yor\w*"
)


def _is_sale_match(match: re.Match[str], clause: str) -> bool:
    if _NON_SALE_CONTRACT.search(clause):
        return False
    return bool(match.group("sale") or match.group("order"))


def issuer_signature(*fields: str) -> ContractKind | None:
    """Dar, olumlu şirket beyanı. Bir ihalenin başlığı imza, her imza da satış değildir."""
    kind: ContractKind | None = None
    for field in fields:
        for clause in split_clauses(field):
            lower = tr_lower(clause)
            if _THIRD_PARTY_ONLY.match(lower) or not _ISSUER_SUBJECT.search(lower):
                continue
            if match := _SIGNATURE.search(lower):
                if _is_sale_match(match, lower):
                    return "sale"
                if not _NON_SALE_CONTRACT.search(lower):
                    kind = "contract"
    return kind


def sale_qualified(*fields: str) -> bool:
    for text in fields:
        cleaned = _NEGATED_QUALIFICATION.sub(" ", tr_lower(text))
        if _QUALIFICATION.search(cleaned):
            return True
    return False


def stoppage_confirmed(quote: str) -> bool:
    """Alıntı gerçekten olumlu bir üretim/faaliyet duruşu bildiriyor mu?"""
    lower = tr_lower(quote)
    return bool(_STOPPAGE_AFFIRMATIVE.search(lower)) and not bool(_STOPPAGE_CONTRADICTION.search(lower))


def stoppage_contradicted(*fields: str) -> bool:
    """Alıntı veya sonraki metin duruşu yalanlıyor ya da yeniden başlamayı bildiriyor mu?"""
    return any(_STOPPAGE_CONTRADICTION.search(tr_lower(text)) for text in fields if text)


def polarity_inverted(quote: str, *reading_parts: str) -> bool:
    """Kaynak açıkça 'yok/bulunmamaktadır' derken okuma tümüyle olumlu iddia mı kuruyor?"""
    if not _SOURCE_DENIAL.search(tr_lower(quote)):
        return False
    combined = tr_lower(" ".join(p for p in reading_parts if p))
    return not bool(_READING_NEGATION.search(combined))


@dataclass(frozen=True, slots=True)
class EventAmount:
    money: Money | None = None
    clause: str = ""  # money.start/end bu özgün parçaya göre
    issue: str | None = None

    def as_try(self, fx: Mapping[str, Decimal] | None = None) -> tuple[Decimal | None, tuple[str, ...]]:
        if self.money is None:
            assert self.issue is not None
            return None, (self.issue,)
        value = self.money.to_try(fx)
        return (None, ("fx_fallback",)) if value is None else (value, ())


def event_amount(*fields: str, contract: bool = False) -> EventAmount:
    """Farklı olası bedeller arasında büyüklük veya kur bulunurluğu ile seçim YOK.

    Dar istisna: ayrı cümlede açık proje toplamı/birim değer varsa, kalan bedel
    ancak açık şirket payı veya şirketin imzaladığı satış ise kullanılabilir.
    """
    candidates: dict[tuple[str, Decimal], EventAmount] = {}
    other_basis = False
    for field in fields:
        for clause in split_clauses(field):
            amounts = find_amounts(clause)
            if not amounts:
                continue
            lower = tr_lower(clause)
            if _OTHER_BASIS.search(lower) or (contract and _CONTRACT_BACKGROUND.search(lower)):
                if len(amounts) != 1:
                    return EventAmount(issue="ambiguous_amount")
                other_basis = True
                continue
            for money in amounts:
                key = (money.currency, money.amount)
                candidates[key] = EventAmount(money, clause)
    if len(candidates) > 1:
        return EventAmount(issue="ambiguous_amount")
    if not candidates:
        return EventAmount(issue="unattributed_amount" if other_basis else "no_amount")
    (event,) = candidates.values()
    assert event.money is not None
    clause = event.clause
    lower_clause = tr_lower(clause)
    signature = _SIGNATURE.search(lower_clause)
    if signature and _is_sale_match(signature, lower_clause) and issuer_signature(clause) != "sale":
        return EventAmount(issue="unattributed_amount")
    if contract and not (_PRICE_BEFORE.search(tr_lower(clause[:event.money.start]))
                         or _PRICE_AFTER.search(tr_lower(clause[event.money.end:]))):
        return EventAmount(issue="unattributed_amount")
    if other_basis:
        if not (_OWN_SHARE.search(lower_clause) or issuer_signature(clause) == "sale"):
            return EventAmount(issue="unattributed_amount")
    return event
