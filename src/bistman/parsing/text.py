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
    r"|t\.?\s?a\.?\s?o\.?"
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

# KAP sembolü: 3-6 karakter, harfle başlar, rakam içerebilir (A1CAP, A1YEN).
SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,5}\b")
# KAP çok hisseli bildirimlerde semboller başlığın " / " sonrasında sıralanır:
#   "BORSA İSTANBUL BISTECH ... Bildirimi / MTRKS"
#   "BORSA İSTANBUL A.Ş. Hak Kullanımı / KRGYO, MTRKS"
_TITLE_SYMBOL_SPLIT = re.compile(r"\s/\s")

# Noktadan sonra büyük harf gelse bile cümleyi bitirmeyen Türkçe kısaltmalar
# ("Prof. Dr. Ahmet", "ABC Ltd. Şti.", "Bkz. Madde", "Av. Mehmet").
_ALWAYS_ABBREV = frozenset(
    {
        "prof", "dr", "av", "doç", "doc", "yrd", "uzm", "arş", "ars", "öğr", "ogr",
        "sn", "sayın", "bkz", "bknz", "ör", "örn", "orn", "vb", "vs", "vd", "yak",
        "md", "mad", "no", "nr", "tel", "faks", "cad", "sok", "mah", "blv", "bul", "apt",
        "ltd", "koop", "san", "tic", "paz", "ith", "ihr", "inş", "ins", "taah", "tur",
        "müh", "muh", "mim", "elk", "elek", "mak", "oto", "tek", "teks", "gıd", "gid",
        "kim", "sağ", "sag", "hiz", "dan", "nak", "yat", "men", "değ", "deg", "ort",
        "yön", "yon", "kur", "bşk", "bsk", "gen", "müd", "mud", "gn",
    }
)
# Kurum ve resmî baş harf kısaltmaları ("A.Ş.", "T.A.Ş.", "A.O.", "Şti.", "T.C.", "A.B.D.", "S.S."):
# aynı satırda cümle içinde çok sık geçer ("... T.C. Sağlık Bakanlığı arasında ...").
_CORP_ABBREV_RE = re.compile(
    r"(?:(?<=\s)|^)(?:t\.?\s*a\.?\s*[şs]|t\.?\s*a\.?\s*o|a\.?\s*[şs]|a\.?\s*o|[şs]ti)\.$",
    re.IGNORECASE,
)
_DOTTED_INITIALISM_RE = re.compile(
    r"(?:(?<=\s)|^)(?:[^\W\d_]\.\s*){2,}$",
    re.UNICODE,
)
_LAST_DOT_WORD_RE = re.compile(r"([^\W\d_]+)\.$", re.UNICODE)
_SINGLE_INITIAL_RE = re.compile(r"(?:(?<=\s)|^)[^\W\d_]\.$", re.UNICODE)
_ORDINAL_END_RE = re.compile(r"(?:(?<=\s)|^)\d{1,4}\.$")
_ORDINAL_NEXT_RE = re.compile(
    r"^\s*(?:dönem|çeyrek|yıl|ay|hafta|gün|madde|fıkra|bent|tertip|etap|faz|kısım|"
    r"bölüm|grup|derece|sıra|olağan|asliye|icra|idare|ağır|ticaret|hukuk|ceza|noter)\b",
    re.IGNORECASE,
)
_SENT_PUNCT_WS = re.compile(r"(?<=[.!?])[ \t]+|\n+|;[ \t]*")


def _first_alpha(text: str) -> str | None:
    for ch in text.lstrip():
        if ch.isalpha():
            return ch
        return ch
    return None


def is_abbreviation_break(before: str, after: str = "") -> bool:
    """`before` sonundaki nokta gerçek bir cümle sonu değil, kısaltma/sıra noktası mı?"""
    # O(1) pencere: büyük KAP gövdelerinde (50+ KB) tüm öneki kopyalayıp küçük harfe çevirme.
    end = len(before)
    has_newline = False
    while end > 0 and before[end - 1].isspace():
        if before[end - 1] == "\n":
            has_newline = True
        end -= 1
    if end == 0 or before[end - 1] != ".":
        return False
    win_start = max(0, end - 48)
    while win_start > 0 and (end - win_start) < 128 and not before[win_start - 1].isspace():
        win_start -= 1
    tail = before[win_start:end]
    tail_low = tr_lower(tail)
    after_head = after[:64]
    if _CORP_ABBREV_RE.search(tail_low) or _DOTTED_INITIALISM_RE.search(tail_low):
        # "ABC A.Ş. arasında" veya "T.C. Sağlık Bakanlığı" cümle sonu değildir.
        # Yalnız satır sonundan sonra yeni paragraf başlıyorsa cümle sınırı kabul edilir.
        return not has_newline
    if m := _LAST_DOT_WORD_RE.search(tail_low):
        if m.group(1) in _ALWAYS_ABBREV:
            return True
    if _SINGLE_INITIAL_RE.search(tail):
        return True
    if _ORDINAL_END_RE.search(tail):
        nxt = _first_alpha(after_head)
        if (nxt is not None and nxt.islower()) or _ORDINAL_NEXT_RE.match(tr_lower(after_head)):
            return True
    # Herhangi bir noktadan hemen sonra küçük harfle devam ediliyorsa (ör. "12-(4). maddesi")
    nxt = _first_alpha(after_head)
    if nxt is not None and nxt.isalpha() and nxt.islower() and not has_newline:
        return True
    return False


def split_clauses(text: str) -> list[str]:
    """Kısaltma noktalarını (`A.Ş.`, `Ltd. Şti.`, `Prof. Dr.`) bölmeden cümle/yan cümle ayırır."""
    if not text:
        return []
    parts: list[str] = []
    start = 0
    for match in _SENT_PUNCT_WS.finditer(text):
        sep = match.group(0)
        m_start, m_end = match.start(), match.end()
        if "\n" not in sep and not sep.startswith(";"):
            before_win = text[max(0, m_start - 128):m_start]
            after_win = text[m_end:m_end + 64]
            if is_abbreviation_break(before_win, after_win):
                continue
        chunk = text[start:m_start]
        if chunk.strip():
            parts.append(chunk)
        start = m_end
    tail = text[start:]
    if tail.strip():
        parts.append(tail)
    return parts


def symbols_in_title(title: str | None) -> tuple[str, ...]:
    r"""Başlıktaki `/` sonrası sembol listesini döndürür.

    Yalnız `\s/\s` varsa ayrıştırılır: şirket unvanlarındaki "A.Ş." gibi
    kısaltmalar sembol sanılmasın. KAP bu biçimi platform bildirimlerinde
    (devre kesici, tipe dönüşüm, işlem yasağı) kullanıyor; canlı akışta
    ticker alanı olmadığı için tek yol bu.
    """
    if not title or not _TITLE_SYMBOL_SPLIT.search(title):
        return ()
    tail = _TITLE_SYMBOL_SPLIT.split(title)[-1]
    return tuple(SYMBOL_RE.findall(tail))


def tr_lower(text: str) -> str:
    """Türkçe kurallarına uygun küçük harfe çevirir (ayrışık `I\\u0307` / `i\\u0307` dahil)."""
    if not text:
        return ""
    if "\u0307" in text:
        text = text.replace("I\u0307", "i").replace("i\u0307", "i").replace("\u0307", "")
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
