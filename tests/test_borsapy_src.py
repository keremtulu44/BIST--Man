"""Satır seçimi + TTM birleştirme — GERÇEK kalem adları ve GERÇEK sayılarla.

Etiketler kullanıcının makinesindeki doğrulama raporundan alındı
(`docs/dogrulama-sonuc.json`, THYAO + AKBNK, 17.11.2026).
"""

from __future__ import annotations

import pytest

from bistman.sources.borsapy_src import (
    annual_values,
    fold,
    fundamentals_from_frames,
    parse_period_column,
    select_row,
    series_points,
)


class FakeFrame:
    """pandas DataFrame'in kullandığımız yüzeyi: index, columns, .loc[label]."""

    def __init__(self, rows: dict[str, list], columns: list[str]) -> None:
        self._rows = rows
        self.index = list(rows)
        self.columns = columns
        self.loc = _Loc(rows)


class _Loc:
    def __init__(self, rows: dict[str, list]) -> None:
        self._rows = rows

    def __getitem__(self, label: str) -> list:
        return self._rows[label]


# ------------------------------------------------------------------- katlama


def test_fold_handles_turkish() -> None:
    assert fold("NET DÖNEM KÂRI/ZARARI") == "net donem kari zarari"
    assert fold("Faiz Gelirleri") == "faiz gelirleri"


# ---------------------------------------------------------------- satır seçimi


AKBNK_LABELS = [
    "I. FAİZ GELİRLERİ",
    "1.1 Kredilerden Alınan Faizler",
    "1.7 Diğer Faiz Gelirleri",
    "II. FAİZ GİDERLERİ",
    "XII. NET FAİZ GELİRİ (I-II)",
    "XXIII. NET DÖNEM KARI/ZARARI (XVII+XXII)",
]

THYAO_LABELS = [
    "Satış Gelirleri",
    "Satışların Maliyeti (-)",
    "Brüt Kar (Zarar)",
    "NET DÖNEM KAR/ZARARI",
]


def test_bank_revenue_picks_top_level_not_sub_item() -> None:
    """ASIL HATA: '1.7 Diğer Faiz Gelirleri' (alt kalem) seçilirse payda 40x yanlış."""
    pick = select_row(AKBNK_LABELS, "revenue")
    assert pick.label == "I. FAİZ GELİRLERİ"
    assert pick.is_top_level is True


def test_bank_net_income_picks_roman_numeral_item() -> None:
    assert select_row(AKBNK_LABELS, "net_income").label == "XXIII. NET DÖNEM KARI/ZARARI (XVII+XXII)"


def test_industrial_revenue() -> None:
    assert select_row(THYAO_LABELS, "revenue").label == "Satış Gelirleri"


def test_industrial_net_income() -> None:
    """Kullanıcının raporunda THYAO'nun kâr satırı bulunamamıştı; artık bulunuyor."""
    assert select_row(THYAO_LABELS, "net_income").label == "NET DÖNEM KAR/ZARARI"


def test_no_match_returns_none_not_a_guess() -> None:
    pick = select_row(["Aktif Toplamı"], "revenue")
    assert pick.label is None
    assert pick.found is False


def test_sub_item_loses_to_top_level_even_with_extra_keyword() -> None:
    """'Diğer' cezası + alt kalem cezası, anahtar kelime fazlalığını yenmeli."""
    labels = ["I. FAİZ GELİRLERİ", "1.7 Diğer Faiz Gelirleri ve Benzeri"]
    assert select_row(labels, "revenue").label == "I. FAİZ GELİRLERİ"


def test_equity_role() -> None:
    labels = ["Özkaynaklar", "Ana Ortaklığa Ait Özkaynaklar", "Toplam Kaynaklar"]
    assert select_row(labels, "equity").label in {
        "Özkaynaklar",
        "Ana Ortaklığa Ait Özkaynaklar",
    }


def test_candidates_exposed_for_debugging() -> None:
    pick = select_row(AKBNK_LABELS, "revenue")
    assert len(pick.candidates) >= 2


# -------------------------------------------------------------------- dönemler


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("2026Q2", (2026, 2)),
        ("2026Q4", (2026, 4)),
        ("2026Q1", (2026, 1)),
        ("2026/9", (2026, 3)),
        ("2026-06", (2026, 2)),
        ("2025", None),
        ("TTM", None),
    ],
)
def test_parse_period_column(column: str, expected) -> None:
    assert parse_period_column(column) == expected


def test_series_points_skips_annual_columns() -> None:
    columns = ["2026Q2", "2026Q1", "2025Q4", "2025"]
    values = [585.069, 257.961, 1187.816, 1043.0]
    points = series_points(columns, values)
    assert [(p.year, p.quarter) for p in points] == [(2026, 2), (2026, 1), (2025, 4)]


def test_annual_values_maps_years() -> None:
    assert annual_values(["2025", "2024", "TTM"], [1187.816, 1043.0, 2000.0]) == {
        2025: 1187.816,
        2024: 1043.0,
    }


# --------------------------------------------------------------- birleştirme


def _thyao_frames() -> tuple[FakeFrame, FakeFrame]:
    """Gerçek THYAO sayıları (kümülatif YTD). 2025Q2 uydurma (raporda yok)."""
    quarterly = FakeFrame(
        {
            "Satış Gelirleri": [585.069, 257.961, 1187.816, 690.825, 380.0],
            "NET DÖNEM KAR/ZARARI": [120.0, 45.0, 210.0, 130.0, 95.0],
            "Özkaynaklar": [900.0, 860.0, 840.0, 820.0, 800.0],
        },
        ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2"],
    )
    annual = FakeFrame({"Satış Gelirleri": [1187.816, 1043.0]}, ["2025", "2024"])
    return quarterly, annual


def test_fundamentals_uses_correct_ttm_not_borsapy_sum() -> None:
    quarterly, annual = _thyao_frames()
    row = fundamentals_from_frames(ticker="THYAO", quarterly=quarterly, annual=annual)

    assert row.revenue_ttm == pytest.approx(1187.816 + 585.069 - 380.0)  # 1392.885
    assert row.revenue_ttm != pytest.approx(2721.671)  # borsapy'nin 4 sütun toplamı DEĞİL
    assert "borsapy_ttm_mismatch" in row.flags


def test_fundamentals_flags_missing_market_cap() -> None:
    """fast_info BOŞ döndü (gerçek doğrulama) — sessizce geçmemeli."""
    quarterly, annual = _thyao_frames()
    row = fundamentals_from_frames(ticker="THYAO", quarterly=quarterly, annual=annual)
    assert "no_market_cap" in row.flags


def test_fundamentals_market_cap_present_when_given() -> None:
    quarterly, annual = _thyao_frames()
    row = fundamentals_from_frames(
        ticker="THYAO", quarterly=quarterly, annual=annual, market_cap=350e9
    )
    assert "no_market_cap" not in row.flags


def test_fundamentals_handles_missing_roles_without_fabricating() -> None:
    quarterly = FakeFrame({"Aktif Toplamı": [100.0, 90.0]}, ["2026Q2", "2026Q1"])
    row = fundamentals_from_frames(ticker="X", quarterly=quarterly)
    assert row.revenue_ttm is None
    assert "row_not_found:revenue" in row.flags
    assert "no_revenue" in row.flags


def test_fundamentals_net_income_uses_own_row() -> None:
    quarterly, annual = _thyao_frames()
    row = fundamentals_from_frames(ticker="THYAO", quarterly=quarterly, annual=annual)
    assert row.net_income_ttm == pytest.approx(210.0 + 120.0 - 95.0)


def test_fundamentals_bank_template_keeps_statement_type() -> None:
    quarterly = FakeFrame(
        {
            "I. FAİZ GELİRLERİ": [1.004, 0.455, 1.83876, 0.974, 0.78],
            "1.7 Diğer Faiz Gelirleri": [0.05, 0.02, 0.09, 0.04, 0.03],
            "XXIII. NET DÖNEM KARI/ZARARI (XVII+XXII)": [40.0, 18.0, 75.0, 50.0, 33.0],
        },
        ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2"],
    )
    annual = FakeFrame({"I. FAİZ GELİRLERİ": [1.83876]}, ["2025"])
    row = fundamentals_from_frames(
        ticker="AKBNK", quarterly=quarterly, annual=annual, statement_type="bank"
    )
    assert row.statement_type == "bank"
    assert row.revenue_ttm == pytest.approx(1.83876 + 1.004 - 0.78)
    assert row.revenue_ttm > 1.0  # alt kalem seçilseydi 0.09 civarı çıkardı


# ------------------------------- GERÇEK kalem listesi (THYAO, 133 kalem, 17.11.2026)

# `MaliTablo` üç tabloyu birden döndürüyor (bilanço + gelir + nakit akış).
# Aşağıdakiler nakit akışı ve bilanço kalemleriyle karışmış gerçek listeden.
THYAO_REAL_LABELS = [
    "    Uzun vadeli karşılıklar",
    "  Borç Karşılıkları",
    "  Dönem Karı Vergi Yükümlülüğü",
    "  Dönem Net Kar/Zararı",
    "  Geçmiş Yıllar Kar/Zararları",
    "  Kardan Ayrılan Kısıtlanmış Yedekler",
    "DÖNEM KARI (ZARARI)",
    "Dönem Kar/Zararının Dağılımı",
    "SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI/ZARARI",
    "Durdurulan Faaliyetler Vergi Sonrası Dönem Karı (Zararı)",
    "Net Faaliyet Kar/Zararı",
    "BRÜT KAR (ZARAR)",
    "Satış Gelirleri",
    "TOPLAM VARLIKLAR",
    "Özkaynaklar",
]


def test_real_labels_net_income_is_period_profit_not_tax_item() -> None:
    """ASIL HATA: 'Dönem Karı Vergi Yükümlülüğü' (0.5 milyar) net kâr sanıldı."""
    pick = select_row(THYAO_REAL_LABELS, "net_income")
    assert pick.label == "DÖNEM KARI (ZARARI)"
    assert "Vergi" not in pick.label


def test_real_labels_revenue_lock() -> None:
    assert select_row(THYAO_REAL_LABELS, "revenue").label == "Satış Gelirleri"


def test_real_labels_total_assets() -> None:
    assert select_row(THYAO_REAL_LABELS, "total_assets").label == "TOPLAM VARLIKLAR"


def test_real_labels_equity() -> None:
    assert select_row(THYAO_REAL_LABELS, "equity").label == "Özkaynaklar"


def test_tax_item_never_wins_for_net_income_even_alone() -> None:
    """Listede yalnızca tuzak kalem varsa: ya doğru alternatif ya None — asla vergi kalemi."""
    lonely = ["  Dönem Karı Vergi Yükümlülüğü", "  Geçmiş Yıllar Kar/Zararları"]
    pick = select_row(lonely, "net_income")
    assert pick.label is None
