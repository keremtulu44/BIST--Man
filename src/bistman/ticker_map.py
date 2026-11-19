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

    def has_ticker(self, ticker: str) -> bool:
        """Verilen sembol bu çözümleyicinin şirket evreninde tanımlı mı?"""
        code = (ticker or "").strip().upper()
        return bool(code) and any(t == code for _, t in self._names)

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


# Unvanlarda ayırt edici olmayan kelimeler (hukuki ve genel ticaret ekleri).
# Dikkat: "ak" ve "is" (AK YATIRIM vs İŞ YATIRIM) ayırt edici marka köküdür, burada YOKTUR;
# "pazarlama", "yatirim", "holding", "uretim" de grup iştiraklerini ayırdığı için silinmez.
_GENERIC = frozenset(
    {
        "ve", "a", "s", "as", "ao", "ta", "tas", "tao", "tc",
        "anonim", "sirketi", "ticaret", "sanayi", "sanayii", "ltd", "sti",
        "turkiye", "turkiyedeki",
    }
)
# Birçok farklı şirketin unvanında ortak geçen sektör/faaliyet kelimeleri:
# "HALK YATIRIM MENKUL DEĞERLER" ile "İŞ YATIRIM MENKUL DEĞERLER" veya
# "HALK FİNANSAL KİRALAMA" ile "VAKIF FİNANSAL KİRALAMA" yalnız bu kelimeler
# benziyor diye eşleşemez; marka köklerinin de bire-bir uyuşması şarttır.
_SECTOR_WORDS = frozenset(
    {
        "yatirim", "yatirimlari", "menkul", "degerler", "kiymetler",
        "finansal", "finansman", "kiralama", "faktoring",
        "gayrimenkul", "girisim", "sermayesi", "ortakligi",
        "portfoy", "yonetimi", "emeklilik", "hayat", "sigorta",
        "bankasi", "holding", "sirketler", "grubu",
        "enerji", "elektrik", "uretim", "uretimi", "santralleri",
        "teknoloji", "teknolojiler", "teknolojileri",
        "hizmet", "hizmetleri", "iletisim", "bilisim", "sistemleri",
        "gida", "cimento", "otomotiv", "elektronik", "madencilik",
        "insaat", "turizm", "tekstil", "kimya", "tarim",
        "pazarlama", "perakende", "magazalari", "lojistik", "tasimacilik",
        "saglik", "servis", "dis", "ic",
    }
)
# Türkçe çekim ekleri (çoğul/iyelik): "teknoloji"~"teknolojiler", "hizmet"~"hizmetleri".
# Serbest önek eşleşmesi "turk"~"turkcell" veya "enerji"~"enerjisa" hatası üretir.
_INFLECTION_SUFFIXES = frozenset(
    {"i", "si", "u", "su", "ler", "lar", "leri", "lari", "yi", "yu"}
)
MIN_SIGNIFICANT_TOKENS = 2


def _significant_tokens(text: str) -> list[str]:
    return [t for t in text.split() if t not in _GENERIC and len(t) >= 2]


def _token_match(a: str, b: str, *, allow_typo: bool = False) -> bool:
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and long.startswith(short):
        if long[len(short):] in _INFLECTION_SUFFIXES:
            return True
    if allow_typo and len(short) >= 5 and len(long) - len(short) <= 1:
        return SequenceMatcher(None, a, b).ratio() >= 0.88
    return False


def _paired_matches(
    sig_a: list[str], sig_b: list[str], *, allow_typo: bool = False
) -> int:
    """Bire-bir kelime eşleşmesi: aynı kökten iki kelime tek hedefe iki kez sayılamaz."""
    used: set[int] = set()
    matched = 0
    for token in sig_a:
        hit = next(
            (idx for idx, candidate in enumerate(sig_b)
             if idx not in used and _token_match(token, candidate, allow_typo=allow_typo)),
            None,
        )
        if hit is not None:
            used.add(hit)
            matched += 1
    return matched


def _brands_compatible(sig_a: list[str], sig_b: list[str]) -> bool:
    """Sektör kelimeleri çıkarıldığında kalan ayırt edici marka kökleri bire-bir uyuşuyor mu?"""
    brand_a = [t for t in sig_a if t not in _SECTOR_WORDS]
    brand_b = [t for t in sig_b if t not in _SECTOR_WORDS]
    if not brand_a and not brand_b:
        return True
    if len(brand_a) != len(brand_b):
        return False
    return _paired_matches(brand_a, brand_b, allow_typo=True) == len(brand_a)


def _similarity(a: str, b: str) -> float:
    """İki unvan arası benzerlik: marka kökü korumalı harf + simetrik kelime örtüşmesi.

    - Ayırt edici marka kelimeleri uyuşmuyorsa ("AK YATIRIM" vs "İŞ YATIRIM",
      "ZİRAAT YATIRIM" vs "İŞ YATIRIM", "ALARKO TARIM" vs "ALARKO CARRIER")
      ortak sektör/ticaret ekleri skoru şişiremez -> `0.0`.
    - Simetrik (`max`) payda sayesinde halka açık şirketin kelimelerini kapsayan
      bağlı ortaklıklar ("ENERJİSA ÜRETİM SANTRALLERİ", "ARÇELİK PAZARLAMA")
      ana hisseye bağlanmaz; gerçek çekim farkları ("TEKNOLOJİ"~"TEKNOLOJİLER") korunur.
    """
    direct = SequenceMatcher(None, a, b).ratio()
    if direct >= 0.99:
        return direct

    sig_a = _significant_tokens(a)
    sig_b = _significant_tokens(b)
    if not _brands_compatible(sig_a, sig_b):
        return 0.0

    # Genel ticaret ekleri ("sanayi ve ticaret") iki farklı iştirakin harf oranını şişirmesin.
    core_a = " ".join(sig_a) if sig_a else a
    core_b = " ".join(sig_b) if sig_b else b
    char_score = max(
        SequenceMatcher(None, core_a, core_b).ratio(),
        SequenceMatcher(None, " ".join(sorted(sig_a)), " ".join(sorted(sig_b))).ratio()
        if sig_a and sig_b else 0.0,
    )

    if len(sig_a) < MIN_SIGNIFICANT_TOKENS or len(sig_b) < MIN_SIGNIFICANT_TOKENS:
        return char_score

    overlap = _paired_matches(sig_a, sig_b, allow_typo=True) / max(len(sig_a), len(sig_b))
    # Aynı marka altında farklı sektör iştiraki ("ALARKO TARIM" vs "ALARKO GIDA") elenir.
    if overlap < 0.75:
        return min(char_score, overlap)
    return max(char_score, overlap)
