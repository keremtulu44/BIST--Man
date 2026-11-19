"""KAP şirket listesi ayrıştırma — ağ yok, kaçışlı JSON biçimi ile."""

from __future__ import annotations

import asyncio
import json

import pytest

from bistman.sources.kap_companies import (
    CompanyMapError,
    fetch_company_map,
    missing_tickers,
    parse_company_map,
)

# KAP sayfasındaki gerçek biçim: HTML içinde ters bölü kaçışlı JSON parçaları
ESCAPED = (
    '{"mkkMemberOid":"8acae2c575a1b5b60177de29c7177130",'
    '"kapMemberTitle":"MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.",'
    '"relatedMemberTitle":"","stockCode":"MTRKS"}'
)
ESCAPED_MULTI = (
    '{"mkkMemberOid":"aaa111",'
    '"kapMemberTitle":"TÜRKİYE GARANTİ BANKASI A.Ş.",'
    '"relatedMemberTitle":"","stockCode":"GARAN, TGB"}'
)


def page(*fragments: str) -> str:
    """Sayfa gibi paketle: JSON'u ters bölü ile kaçışla."""
    body = "".join(fragment.replace('"', '\\"') for fragment in fragments)
    return f"<html><script>self.__next_f.push([1,\"{body}\"])</script></html>"


def test_parse_single_company() -> None:
    mapping = parse_company_map(page(ESCAPED))
    assert mapping == {"MTRKS": "8acae2c575a1b5b60177de29c7177130"}


def test_parse_multiple_codes_share_one_oid() -> None:
    """'GARAN, TGB' -> iki kod, aynı OID."""
    mapping = parse_company_map(page(ESCAPED_MULTI))
    assert mapping == {"GARAN": "aaa111", "TGB": "aaa111"}


def test_parse_many_companies() -> None:
    mapping = parse_company_map(page(ESCAPED, ESCAPED_MULTI))
    assert set(mapping) == {"MTRKS", "GARAN", "TGB"}


def test_missing_marker_raises() -> None:
    with pytest.raises(CompanyMapError, match="mkkMemberOid yok"):
        parse_company_map("<html>Giriş yapın</html>")


def test_empty_mapping_raises() -> None:
    with pytest.raises(CompanyMapError, match="eşleme boş"):
        parse_company_map("<html>mkkMemberOid ama desen yok</html>")


def test_missing_tickers_reports_gaps() -> None:
    mapping = {"GARAN": "x", "TGB": "x"}
    assert missing_tickers(mapping, ["GARAN", "THYAO", "ASELS"]) == ["THYAO", "ASELS"]


def test_fetch_company_map_uses_injected_client() -> None:
    class FakeKap:
        async def get_text(self, path: str) -> str:
            assert path == "/tr/bist-sirketler"
            return page(ESCAPED)

    mapping = asyncio.run(fetch_company_map(FakeKap()))
    assert mapping["MTRKS"].startswith("8acae2")


def test_parse_companies_builds_live_title_resolver() -> None:
    from bistman.sources.kap_companies import build_company_resolver, parse_companies

    entries = parse_companies(page(ESCAPED, ESCAPED_MULTI))
    resolver = build_company_resolver(entries)
    assert resolver.resolve("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.").ticker == "MTRKS"
    # Aynı üyenin 'GARAN, TGB' ikincil kodu kendi unvanını belirsizliğe düşürmemeli.
    assert resolver.resolve("TÜRKİYE GARANTİ BANKASI A.Ş.").ticker == "GARAN"
    filtered = build_company_resolver(entries, universe=["MTRKS"])
    assert filtered.resolve("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.").ticker == "MTRKS"
    assert filtered.resolve("TÜRKİYE GARANTİ BANKASI A.Ş.").ticker is None
