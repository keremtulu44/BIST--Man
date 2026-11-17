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
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
}

# Sıra KRİTİK: binlik-gruplu -> EN ondalık -> TR ondalık -> düz.
# EN ondalık, "3.5 milyar"daki "3.5"i yakalayabilmek için düz sayıdan ÖNCE gelir;
# aksi hâlde motor "3"ü eşleyip ".5 milyar"ı atlar ve tutarı 5 milyar okur.
_NUM = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+\.\d{1,2}|\d+(?:,\d+)?)"
_MAG = r"(?:bin|milyon|milyar|trilyon)"
_CUR = (
    r"(?:TL|TRY|₺|USD|EUR|GBP|₤|£|\$|dolar|euro|avro|sterlin|türk\s+lirası|abd\s+doları)"
)

# (?<![\d.,]) -> bir sayının ORTASINDAN eşleşmeye başlamayı engeller
# curpre -> "₺1.500.000", "$ 5 milyon" gibi ÖN EK para birimleri
MONEY_RE = re.compile(
    rf"(?<![\d.,])(?P<curpre>{_CUR})?\s*(?P<num>{_NUM})\s*(?P<mag>{_MAG})?\s*(?P<cur>{_CUR})?\b",
    re.IGNORECASE,
)

# "1.234.567,89 TL" gibi binlik gruplu sayı
_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")
# "1234,56" -> virgül ondalık (TR)
_TR_DECIMAL = re.compile(r"\d+,\d+")
# "3.5" -> nokta ondalık (EN sızması); binlik grubu DEĞİL
_EN_DECIMAL = re.compile(r"\d+\.\d{1,2}")
_PLAIN = re.compile(r"\d+")

YEAR_MIN, YEAR_MAX = 1900, 2100


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal          # ham birimde (magnitude uygulanmış), ör. 500_000_000
    currency: str            # 'TRY' | 'USD' | 'EUR' | 'GBP' | 'UNKNOWN'
    raw: str                 # eşleşen ham metin (audit için)
    start: int
    end: int
    warnings: tuple[str, ...] = ()

    def to_try(self, fx: dict[str, Decimal] | None = None) -> Decimal | None:
        fx = fx or {}
        if self.currency == "TRY":
            return self.amount
        rate = fx.get(self.currency)
        if rate is None:
            return None
        return (self.amount * rate).quantize(Decimal("0.01"))


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

    # Yüzde ifadelerini maskele: "%12,5", "% 3", "yüzde 12"
    masked = re.sub(r"%\s*\d+(?:[.,]\d+)?", " " * 0, text)
    masked = re.sub(r"yüzde\s+\d+(?:[.,]\d+)?", "", masked, flags=re.IGNORECASE)

    out: list[Money] = []

    for m in MONEY_RE.finditer(masked):
        num_tok = m.group("num")
        mag_tok = (m.group("mag") or "").lower()
        # ön ek ya da son ek para birimi (₺1.500.000 | 1.500.000 TL)
        cur_tok = ((m.group("cur") or m.group("curpre")) or "").lower()

        # Kural 1: ne büyüklük ne para birimi varsa -> tutar değil
        if not mag_tok and not cur_tok:
            continue

        try:
            value, warns = _parse_number(num_tok)
        except (ValueError, InvalidOperation):
            continue

        # Kural 2: yıl sanısı (ör. "2024 milyon TL" gibi absürtlük yerine
        # sadece tek başına 4 haneli yılları ele; büyüklük varsa dokunma)
        if (
            not mag_tok
            and YEAR_MIN <= value <= YEAR_MAX
            and value == int(value)
            and "," not in num_tok
            and "." not in num_tok
        ):
            continue

        # Kural 3: büyüklük kelimesi uygula
        if mag_tok:
            value = value * MAGNITUDE[mag_tok]

        currency = CURRENCY.get(cur_tok, "UNKNOWN")

        # Kural 4: para birimi yok ama büyüklük var -> "15 milyar" (TL varsayımı
        # yapma, işaretle; FX kararı çağıran katmanın)
        warns_list = list(warns)
        if currency == "UNKNOWN":
            warns_list.append("para_birimi_yok")

        out.append(
            Money(
                amount=value,
                currency=currency,
                raw=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
                warnings=tuple(warns_list),
            )
        )

    return out


def best_amount(text: str, fx: dict[str, Decimal] | None = None) -> Money | None:
    """TRY değeri en büyük tutarı seç (KAP metinlerinde ana tutar genelde en büyüktür)."""
    candidates = find_amounts(text)
    if not candidates:
        return None

    def key(m: Money) -> tuple[int, Decimal]:
        try_try = m.to_try(fx)
        # TRY'ye çevrilebilenler önce gelir
        return (1 if try_try is not None else 0, try_try or m.amount)

    return max(candidates, key=key)
