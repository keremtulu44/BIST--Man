"""Tutar parser testleri — sistemin en yüksek riskli saf fonksiyonu.

Buradaki her satır KAP/iş yatırım metinlerinde gerçekten görülen bir kalıp.
Yeni bir tuzak bulduğunda ÖNCE buraya test yaz, sonra kodu düzelt.
"""

from decimal import Decimal

import pytest

from bistman.parsing.money import best_amount, find_amounts

FX = {"USD": Decimal("34.50"), "EUR": Decimal("37.10")}


@pytest.mark.parametrize(
    ("text", "expected", "currency"),
    [
        ("Şirketimiz 15.000.000.000 TL tutarında yeni sözleşme imzalamıştır.",
         Decimal("15000000000"), "TRY"),
        ("1,2 milyar TL tutarında yatırım kararı alınmıştır.", Decimal("1200000000"), "TRY"),
        ("500 milyon dolar tutarında kredi sağlanmıştır.", Decimal("500000000"), "USD"),
        ("1.234,56 TL ceza kesilmiştir.", Decimal("1234.56"), "TRY"),
        ("Şirket 250 bin TL sermaye artırımı yapacaktır.", Decimal("250000"), "TRY"),
        ("₺1.500.000 bedelli sermaye artırımı yapılacaktır.", Decimal("1500000"), "TRY"),
        ("Net kâr 2,3 katına çıkarak 8,4 milyar TL oldu.", Decimal("8400000000"), "TRY"),
        ("Toplam 1.500.000.000,00 TL tutarında iki ayrı anlaşma.", Decimal("1500000000"), "TRY"),
        ("Sözleşme bedeli 4 milyar TL olup 3 yıla yayılmıştır.", Decimal("4000000000"), "TRY"),
    ],
)
def test_parses_amounts(text: str, expected: Decimal, currency: str) -> None:
    money = best_amount(text, FX)
    assert money is not None, text
    assert money.amount == expected
    assert money.currency == currency


@pytest.mark.parametrize(
    "text",
    [
        "%12,5 oranında artış beklenmektedir.",          # yüzde
        "yüzde 12 oranında artış beklenmektedir.",       # yüzde (kelimeyle)
        "2024 yılında faaliyetlerine devam etmiştir.",   # yıl
        "Yönetim Kurulu 12 üyeden oluşmaktadır.",        # adet
        "1.234 adet pay satılmıştır.",                   # adet (binlik gruplu!)
        "17.11.2026 tarihli açıklama.",                  # tarih
        "Haber metninde hiç tutar yok.",
        "",
    ],
)
def test_rejects_non_money(text: str) -> None:
    assert find_amounts(text) == []


def test_usd_converted_only_with_rate() -> None:
    money = best_amount("500 milyon dolar tutarında kredi", FX)
    assert money is not None
    assert money.to_try(FX) == Decimal("17250000000.00")
    # Kur verilmezse uydurmuyor, None dönüyor
    assert money.to_try({}) is None


def test_en_decimal_leak_is_flagged() -> None:
    """'3.5 milyar' TR metnine EN formatı sızması: 5 milyar değil 3.5 milyar okunmalı."""
    money = best_amount("3.5 milyar TL'lik ihale kazanıldı.", FX)
    assert money is not None
    assert money.amount == Decimal("3500000000.0")
    assert "en_format_decimal:3.5" in money.warnings


def test_multiple_amounts_returns_all() -> None:
    found = find_amounts("1.500.000.000,00 TL ve 250 milyon Euro tutarında iki anlaşma")
    assert [m.currency for m in found] == ["TRY", "EUR"]


def test_best_amount_prefers_converted_value() -> None:
    """Hem TRY hem USD varsa, TRY'ye çevrilebilen daha büyük olan seçilir."""
    money = best_amount("100 milyon TL ve 50 milyon dolar", FX)  # 100M TL vs ~1.7B TL
    assert money is not None
    assert money.currency == "USD"
