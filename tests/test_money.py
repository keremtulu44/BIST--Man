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
        # 'Avro' KAP metinlerinde yaygın; eşanlamlı listesinde yoktu -> no_amount
        ("45 milyon Avro tutarında sözleşme", Decimal("45000000"), "EUR"),
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


@pytest.mark.parametrize(
    ("phrase", "amount", "currency"),
    [
        ("5 MİLYAR TÜRK LİRASI", "5000000000", "TRY"),
        ("5 MILYON TL", "5000000", "TRY"),
        ("3 BİN ABD\u00a0DOLARI", "3000", "USD"),
        ("€3.500,50", "3500.50", "EUR"),
        ("1,2 milyon €", "1200000", "EUR"),
        ("250 $", "250", "USD"),
        ("2000 TL", "2000", "TRY"),  # çıplak yıl değil: birim açık
    ],
)
def test_amount_can_be_traced_to_original_text(phrase: str, amount: str, currency: str) -> None:
    text = f"Payı %12,5 (yüzde 12,5); açıklanan tutar: {phrase}."
    [money] = find_amounts(text)
    assert money.amount == Decimal(amount) and money.currency == currency
    assert text[money.start:money.end] == money.raw == phrase


def test_percent_does_not_join_unrelated_number_and_currency() -> None:
    assert find_amounts("10 %25 TL") == []


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


@pytest.mark.parametrize(("text", "expected", "currency"), [
    ("Sözleşme bedeli 250.000.000.- TL'dir.", Decimal("250000000"), "TRY"),
    ("Sözleşme bedeli 1.500,- EUR olarak belirlendi.", Decimal("1500"), "EUR"),
    ("Toplam 12.500.000 CHF tutarında ihracat.", Decimal("12500000"), "CHF"),
    ("Toplam 5 milyon İsviçre Frangı bedelli sipariş.", Decimal("5000000"), "CHF"),
    ("Bedel 1,234,567 USD olarak belirlenmiştir.", Decimal("1234567"), "USD"),
    ("Bedel 12,500.50 USD olarak belirlenmiştir.", Decimal("12500.50"), "USD"),
])
def test_kap_dash_notation_chf_and_en_grouped_amounts(
    text: str, expected: Decimal, currency: str
) -> None:
    [money] = find_amounts(text)
    assert money.amount == expected and money.currency == currency
