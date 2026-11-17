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

# Hukuki ekler yalnızca unvanın SONUNDA atılır (metnin ortasındaki "A.Ş." kalsın).
# Hem Türkçe hem ASCII yazım: A.Ş. / A.S. / T.A.Ş. / TAS / A.O. / LTD.ŞTİ.
_LEGAL_SUFFIX = re.compile(
    r"(?:(?<=\s)|^)(?:"
    r"a\.?\s?[şs]\.?"
    r"|a\.?\s?o\.?"
    r"|t\.?\s?a\.?\s?[şs]\.?"
    r"|anonim\s+[şs]irketi"
    r"|limited\s+[şs]irketi"
    r"|ltd\.?\s?[şs]ti\.?"
    r")\s*$",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)

# Eşleştirme için ASCII katlama: KAP unvanları Türkçe karakterle yazılı
# ("MATRİKS") ama kullanıcı/kaynak verisi ASCII yazabiliyor ("MATRIKS").
# tr_lower() Türkçe kuralına sadık kalır (I->ı); name_norm() eşleştirme
# anahtarı olduğu için diakritikleri sadeleştirir — ikisi aynı anahtara düşer.
_ASCII_FOLD = str.maketrans(
    {"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"}
)
_SPACES = re.compile(r"\s+")


def tr_lower(text: str) -> str:
    """Türkçe kurallarına uygun küçük harfe çevirir."""
    if not text:
        return ""
    text = text.translate(_TR_MAP)
    text = text.replace("â", "a").replace("î", "i").replace("û", "u")
    return text.lower()


def name_norm(text: str) -> str:
    """Şirket unvanı eşleştirme anahtarı.

    TR-küçük harf + ASCII katlama + hukuki ek atma + noktalama temizliği:
        "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş." -> "matriks finansal teknolojiler"
        "MATRIKS FINANSAL TEKNOLOJI"          -> "matriks finansal teknoloji"
    """
    if not text:
        return ""
    # Hukuki ek her durumda atılır (bu fonksiyon düz metne değil, yalnızca
    # unvan anahtarına uygulanır). İki tur: "T.A.Ş." -> "A.Ş." gibi zincirler için.
    for _ in range(2):
        new_text = _LEGAL_SUFFIX.sub("", text).strip()
        if new_text == text:
            break
        text = new_text
    text = tr_lower(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ASCII_FOLD)
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()
