"""Olaya ait bedel: sayıyı bulmak, kime/neye ait olduğunu belirlemek değildir."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .money import Money, find_amounts
from .text import tr_lower

# Tutarın başlık, özet ve gövde arasında 'tamamlanmasını' önlemek için alanlar
# ayrı taranır. Ondalık/binlik noktası cümle sonu değildir.
_CLAUSE_END = re.compile(r"(?<=[.!?])\s+|;\s*")
_OTHER_BASIS = re.compile(
    r"\b(?:proje(?:nin)?|konsorsiyum(?:un)?)\s+(?:toplam\s+)?(?:bütçe|bedel|tutar)"
    r"|\b(?:pay|hisse) başına\b"
)
_CONTRACT_BACKGROUND = re.compile(r"\b(?:ödenmiş sermaye|sermayemiz|ciromuz|geçici teminat)\w*")
_OWN_SHARE = re.compile(r"\b(?:şirketimiz(?:e düşen pay|in payı)|payımıza düşen|payımız\b)")
_PRICE_BEFORE = re.compile(
    r"\b(?:(?:sözleşme(?:nin)?|satış|sipariş(?:in)?|ihale(?:nin)?)\s+(?:toplam\s+)?(?:bedeli|tutarı)"
    r"|şirketimize düşen pay|şirketimizin payı|payımıza düşen (?:bedel|tutar))\s*[:=]?\s*$"
)
_PRICE_AFTER = re.compile(r"^\s*(?:bedelli|tutarında)\s+(?:yeni\s+)?(?:satış\s+)?(?:sözleşme|sipariş|ihale)")
_SIGNATURE = re.compile(r"\b(?P<sale>satış )?sözleşme(?:si(?:ni)?|yi)?\s+imza(?:landı|lanmıştır|ladı|ladık|lamıştır)\b")
ContractKind = Literal["sale", "contract"]
# Dar koruma: geçmiş bir imzayı anlatan cümleden sonraki feshi/koşulu koparma.
# Eşleşmemesi, hukukî kesinlik garantisi değildir.
_QUALIFICATION = re.compile(
    r"\b(?:iptal|fesih|fesh|feshet|feshed|imzalanmadı|imzalanmamış|imzalamadı|yalanla|iddia)"
    r"|gerçeği yansıtm|bağlayıcı değil|(?:onayına|koşuluna|şartına) bağlı"
)


def issuer_signature(*fields: str) -> ContractKind | None:
    """Dar, olumlu şirket beyanı. Bir ihalenin başlığı imza, her imza da satış değildir."""
    kind: ContractKind | None = None
    for field in fields:
        for clause in _CLAUSE_END.split(tr_lower(field)):
            if clause.strip().startswith("şirketimiz ") and (match := _SIGNATURE.search(clause)):
                if match["sale"]:
                    return "sale"
                kind = "contract"
    return kind


def sale_qualified(*fields: str) -> bool:
    return any(_QUALIFICATION.search(tr_lower(text)) for text in fields)


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
        for clause in _CLAUSE_END.split(field):
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
    signature = _SIGNATURE.search(tr_lower(clause))
    if signature and signature["sale"] and not issuer_signature(clause):
        return EventAmount(issue="unattributed_amount")
    if contract and not (_PRICE_BEFORE.search(tr_lower(clause[:event.money.start]))
                         or _PRICE_AFTER.search(tr_lower(clause[event.money.end:]))):
        return EventAmount(issue="unattributed_amount")
    if other_basis:
        if not (_OWN_SHARE.search(tr_lower(clause)) or issuer_signature(clause) == "sale"):
            return EventAmount(issue="unattributed_amount")
    return event
