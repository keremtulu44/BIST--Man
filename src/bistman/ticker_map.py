"""Unvan -> hisse kodu eşlemesi.

Neden gerekli: `/api/disclosure/list/light` **ticker vermiyor**, sadece şirket
unvanı veriyor ("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."). Detay ucu ticker verse de
bildirim başına 1 istek demek (günde ~1000). Doğru çözüm: unvanı yerelde eşle,
sadece **eşleşen** bildirim için detay ucunu çağır.

Katmanlar (MIMARI.md §3.3'ün sadeleşmiş hâli):
    1. exact  -> name_norm birebir (güven 1.00)
    2. fuzzy  -> difflib oranı >= cutoff (güven = oran)
    3. none   -> eşleşmedi; çağıran 'unresolved' kuyruğuna atar

Belirsizlik kuralı: iki aday cutoff üstünde ve skorları 2 puan içindeyse
**eşleştirme yapılmaz**. Yanlış hisseye bildirim göndermek, göndermemekten kötüdür.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Literal

from .parsing.text import name_norm

Method = Literal["exact", "fuzzy", "none", "ambiguous"]
DEFAULT_CUTOFF = 0.88
AMBIGUITY_MARGIN = 0.02


@dataclass(frozen=True, slots=True)
class Resolution:
    ticker: str | None
    method: Method
    score: float = 0.0
    candidates: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TickerResolver:
    """`from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."), ...])`"""

    _exact: dict[str, str]
    _names: tuple[tuple[str, str], ...]  # (name_norm, ticker)
    _collisions: tuple[str, ...] = ()  # aynı anahtara düşen farklı kodlar
    cutoff: float = DEFAULT_CUTOFF

    @classmethod
    def from_pairs(
        cls, pairs: Iterable[tuple[str, str]], *, cutoff: float = DEFAULT_CUTOFF
    ) -> TickerResolver:
        exact: dict[str, str] = {}
        collisions: set[str] = set()
        names: list[tuple[str, str]] = []
        for ticker, title in pairs:
            key = name_norm(title)
            if not key:
                continue
            if (existing := exact.get(key)) is not None and existing != ticker:
                # ASCII katlama iki farklı şirketi aynı anahtara düşürdüyse
                # tahmin etme: anahtarı exact eşleşmeden çıkar.
                collisions.add(key)
            exact[key] = ticker
            names.append((key, ticker))
        for key in collisions:
            exact.pop(key, None)
        return cls(
            _exact=exact,
            _names=tuple(names),
            _collisions=tuple(sorted(collisions)),
            cutoff=cutoff,
        )

    def __len__(self) -> int:
        return len(self._exact)

    def resolve(self, title: str) -> Resolution:
        key = name_norm(title)
        if not key:
            return Resolution(None, "none")

        if key in self._collisions:
            return Resolution(None, "ambiguous", 1.0)

        if (hit := self._exact.get(key)) is not None:
            return Resolution(hit, "exact", 1.0)

        scored = sorted(
            ((_similarity(key, candidate), ticker) for candidate, ticker in self._names),
            reverse=True,
        )
        if not scored:
            return Resolution(None, "none")

        best_score, best_ticker = scored[0]
        if best_score < self.cutoff:
            return Resolution(None, "none", best_score)

        close = [(s, t) for s, t in scored[:2] if best_score - s <= AMBIGUITY_MARGIN]
        if len({t for _s, t in close}) > 1:
            return Resolution(
                None, "ambiguous", best_score, tuple(t for _s, t in close)
            )
        return Resolution(best_ticker, "fuzzy", best_score)


# Unvanlarda ayırt edici olmayan kelimeler (her şirkette geçebilir).
# Bunlar eşleşme puanına sayılmaz; yoksa "SANAYİ VE TİCARET" her şirkete benzer.
_GENERIC = frozenset(
    {
        "ve", "a", "s", "as", "ao", "anonim", "sirketi", "ticaret", "sanayi",
        "ltd", "sti", "holding", "yatirim", "uretim", "pazarlama", "dis",
        "turkiye", "turkiyedeki", "grup",
    }
)
MIN_SIGNIFICANT_TOKENS = 2


def _significant_tokens(text: str) -> list[str]:
    return [t for t in text.split() if t not in _GENERIC and len(t) >= 3]


def _token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    # Türkçe çekim eki toleransı: "teknoloji" ~ "teknolojiler", "gida" ~ "gidalar"
    if len(a) >= 4 and len(b) >= 4:
        return a.startswith(b) or b.startswith(a)
    return False


def _similarity(a: str, b: str) -> float:
    """İki unvan arası benzerlik: harf benzerliği + kelime kapsama, en iyisi kazanır.

    Neden kelime kapsama: harf bazlı oran, "TEKNOLOJI" ile "TEKNOLOJILER" arasında
    hak ettiğinden düşük puan veriyor (0.84) ve doğru eşleşme kaçıyor. Kelime
    kapsama bunu düzeltir: {matriks, finansal, teknoloji} kümesi adayın
    çekirdeğini tamamen kapsıyor.
    """
    direct = SequenceMatcher(None, a, b).ratio()
    if direct >= 0.99:
        return direct

    sorted_a = " ".join(sorted(a.split()))
    sorted_b = " ".join(sorted(b.split()))
    best = max(direct, SequenceMatcher(None, sorted_a, sorted_b).ratio())

    sig_a = _significant_tokens(a)
    sig_b = _significant_tokens(b)
    if len(sig_a) < MIN_SIGNIFICANT_TOKENS or len(sig_b) < MIN_SIGNIFICANT_TOKENS:
        return best

    matched = sum(1 for x in sig_a if any(_token_match(x, y) for y in sig_b))
    containment = matched / min(len(sig_a), len(sig_b))
    return max(best, containment)
