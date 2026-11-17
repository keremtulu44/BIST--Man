"""Türkçe metin normalizasyonu.

Neden ayrı modül: `str.lower()` Türkçe'de yanlış.
    "İ".lower() -> "i̇"  (i + U+0307 birleşik nokta)  → "İŞ BANKASI" != "iş bankası"
    "I".lower() -> "i"                                 → "IŞIK" != "ışık"
Bu yüzden TÜM karşılaştırmalar (unvan→ticker eşleme, triyaj, sektör) buradan geçer.
"""

from __future__ import annotations

import re
import unicodedata

_TR_MAP = str.maketrans(
    {
        "İ": "i",
        "I": "ı",
        "Ş": "ş",
        "Ğ": "ğ",
        "Ü": "ü",
        "Ö": "ö",
        "Ç": "ç",
    }
)

# Şirket unvanlarında sık geçen, eşleşmeyi bozan ekler
_LEGAL_SUFFIX = re.compile(
    r"\b(a\.?\s?ş\.?|a\.?o\.?|anonim\s+şirketi|limited\s+şirketi|ltd\.?\s?şti\.?|t\.?a\.?ş\.?)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def tr_lower(text: str) -> str:
    """Türkçe kurallarına uygun küçük harfe çevirir."""
    if not text:
        return ""
    text = text.translate(_TR_MAP)
    text = text.replace("â", "a").replace("î", "i").replace("û", "u")
    return text.lower()


def name_norm(text: str) -> str:
    """Şirket unvanı eşleştirme anahtarı: TR-küçük, hukuki ek atılmış, noktalama yok.

    "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş." -> "matriks finansal teknolojiler"
    """
    if not text:
        return ""
    # Sadece BÜYÜK harfle yazılmış unvanlarda ek at (küçük harfli metni bozmayalım)
    if text.isupper() or text.upper() == text:
        text = _LEGAL_SUFFIX.sub(" ", text)
    text = tr_lower(text)
    text = unicodedata.normalize("NFKC", text)
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()
