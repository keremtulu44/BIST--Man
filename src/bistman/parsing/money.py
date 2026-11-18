"""Türkçe finans metinlerinden parasal tutar çıkarımı.

Sistemin en yüksek hata riski taşıyan saf fonksiyonu: ağ/DB/LLM bağımlılığı YOK.
Testleri: tests/test_money.py

Tasarım kuralı:
    Para birimi (TL/USD/EUR...) VEYA büyüklük kelimesi (bin/milyon/milyar)
    yoksa sayı TUTAR SAYILMAZ.  "2024 yılında", "%12,5", "1.234 adet" -> elenir.

FX notu: `to_try()` yalnızca verilen kur sözlüğüyle çevirir. Kur yoksa None döner
(uydurma kur yok). Çağıran katman (features.py) bunu confidence cezasına çevirir.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation

from .text import tr_lower

# --- Sabitler ---------------------------------------------------------------

MAGNITUDE = {
    "bin": Decimal(10) ** 3,
    "milyon": Decimal(10) ** 6,
    "milyar": Decimal(10) ** 9,
    "trilyon": Decimal(10) ** 12,
}

CURRENCY = {
    "tl": "TRY", "try": "TRY", "₺": "TRY", "türk lirası": "TRY",
    "usd": "USD", "dolar": "USD", "$": "USD", "abd doları": "USD",
    "eur": "EUR", "euro": "EUR", "€": "EUR", "avro": "EUR",
    "gbp": "GBP", "sterlin": "GBP", "£": "GBP",
    "₤": "UNKNOWN",  # genel lira işareti, TRY varsayma
}

# Sıra KRİTİK: binlik-gruplu -> EN ondalık -> TR ondalık -> düz.
# EN ondalık, "3.5 milyar"daki "3.5"i yakalayabilmek için düz sayıdan ÖNCE gelir;
# aksi hâlde motor "3"ü eşleyip ".5 milyar"ı atlar ve tutarı 5 milyar okur.
_NUM = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+\.\d{1,2}|\d+(?:,\d+)?)"
_MAG = "(?:" + "|".join(MAGNITUDE) + ")"
_CUR = "(?:" + "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in CURRENCY) + ")"


def _unit_key(token: str) -> str:
    """Yalnız eşleşen birimi katla (MİLYON/MILYON); kaynak metne dokunma."""
    return " ".join(tr_lower(token).replace("ı", "i").split())


_CURRENCY_BY_KEY = {_unit_key(k): v for k, v in CURRENCY.items()}

# Yüzdeleri aynı taramada tüket: metinden silmek hem ofsetleri kaydırır hem
# de yüzde ifadesinin iki yanındaki bağımsız sayı ve para birimini birleştirir.
MONEY_RE = re.compile(
    rf"(?P<percent>(?:%\s*|\byüzde\s+){_NUM})|"
    rf"(?<![\w.,])(?:(?P<curpre>{_CUR})\s*)?(?P<num>{_NUM})"
    rf"(?:\s*(?P<mag>{_MAG}))?(?:\s*(?P<cur>{_CUR}))?(?!\w|[.,]\d)",
    re.IGNORECASE,
)

# "1.234.567,89 TL" gibi binlik gruplu sayı
_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")
# "1234,56" -> virgül ondalık (TR)
_TR_DECIMAL = re.compile(r"\d+,\d+")
# "3.5" -> nokta ondalık (EN sızması); binlik grubu DEĞİL
_EN_DECIMAL = re.compile(r"\d+\.\d{1,2}")
_PLAIN = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal          # ham birimde (magnitude uygulanmış), ör. 500_000_000
    currency: str            # 'TRY' | 'USD' | 'EUR' | 'GBP' | 'UNKNOWN'
    raw: str                 # eşleşen ham metin (audit için)
    start: int
    end: int
    warnings: tuple[str, ...] = ()

    def to_try(self, fx: Mapping[str, Decimal] | None = None) -> Decimal | None:
        fx = fx or {}
        if self.currency == "TRY":
            return self.amount
        rate = fx.get(self.currency)
        if self.currency == "UNKNOWN" or rate is None:
            return None
        try:
            rate = Decimal(str(rate))
            if not rate.is_finite() or rate <= 0:
                return None
            return (self.amount * rate).quantize(Decimal("0.01"))
        except DecimalException:
            return None


# --- Sayı normalizasyonu ----------------------------------------------------

def _parse_number(tok: str) -> tuple[Decimal, tuple[str, ...]]:
    """TR (1.234.567,89) ve EN (3.5) formatlarını ayırt ederek Decimal üret."""
    warns: list[str] = []
    if _GROUPED.fullmatch(tok):
        # 1.234.567 veya 1.234.567,89
        return Decimal(tok.replace(".", "").replace(",", ".")), ()
    if _TR_DECIMAL.fullmatch(tok):
        return Decimal(tok.replace(",", ".")), ()
    if _EN_DECIMAL.fullmatch(tok):
        # Nokta ondalık: TR metninde 3.5 yerine 3,5 beklenir -> uyarı üret
        warns.append(f"en_format_decimal:{tok}")
        return Decimal(tok), tuple(warns)
    if _PLAIN.fullmatch(tok):
        return Decimal(tok), ()
    # Aşırı uzun / bozuk sayı: binlik ayırıcısı yanlış kullanılmış olabilir
    try:
        return Decimal(re.sub(r"[^\d]", "", tok)), (f"bozuk_sayi:{tok}",)
    except InvalidOperation:  # pragma: no cover
        raise ValueError(f"sayı çözümlenemedi: {tok!r}")


# --- Ana fonksiyon ---------------------------------------------------------

def find_amounts(text: str) -> list[Money]:
    """Metindeki tüm parasal tutarları döndürür (metin sırasıyla)."""
    if not text:
        return []

    out: list[Money] = []

    for m in MONEY_RE.finditer(text):
        if m.group("percent"):
            continue
        num_tok = m.group("num")
        mag_tok = _unit_key(m.group("mag") or "")
        cur_tok = _unit_key(m.group("cur") or m.group("curpre") or "")

        # Ne büyüklük ne para birimi varsa -> tutar değil
        if not mag_tok and not cur_tok:
            continue

        try:
            value, warns = _parse_number(num_tok)
        except (ValueError, InvalidOperation):
            continue

        if mag_tok:
            value = value * MAGNITUDE[mag_tok]

        currency = _CURRENCY_BY_KEY.get(cur_tok, "UNKNOWN")

        # Para birimi yok ama büyüklük var -> "15 milyar" (TL varsayımı
        # yapma, işaretle; FX kararı çağıran katmanın)
        warns_list = list(warns)
        if currency == "UNKNOWN":
            warns_list.append("para_birimi_yok")

        out.append(
            Money(
                amount=value,
                currency=currency,
                raw=m.group(0),
                start=m.start(),
                end=m.end(),
                warnings=tuple(warns_list),
            )
        )

    return out


def best_amount(text: str, fx: Mapping[str, Decimal] | None = None) -> Money | None:
    """Sayısal maksimum (inceleme aracı); olaya/şirkete ait bedeli belirlemez."""
    candidates = find_amounts(text)
    if not candidates:
        return None

    def key(m: Money) -> tuple[int, Decimal]:
        try_try = m.to_try(fx)
        # TRY'ye çevrilebilenler önce gelir
        return (1 if try_try is not None else 0, try_try or m.amount)

    return max(candidates, key=key)
