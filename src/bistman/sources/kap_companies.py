"""KAP şirket listesi — **unvan/kod -> mkkMemberOid** eşlemesi.

Neden gerekli: backfill `bildirim-sorgu-sonuc?member=OID` ucundan yapılıyor ve
OID'yi bilmek şart. Elimizde tek bir OID vardı (MTRKS). KAP'ın
`/tr/bist-sirketler` sayfası tüm şirketleri **Next.js payload'ında** taşıyor;
kod ve OID oradan çıkar.

Sayfa biçimi (kaçışlı JSON, HTML içinde):

    \\"mkkMemberOid\\":\\"8acae2c575a1b5b60177de29c7177130\\",
    \\"kapMemberTitle\\":\\"MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.\\",
    \\"relatedMemberTitle\\":\\"\\",
    \\"stockCode\\":\\"MTRKS\\"

`stockCode` çoklu olabilir: `"GARAN, TGB"` -> iki kod aynı OID'e bağlanır.

Bu modül yalnız **ayrıştırma** yapar (ağ yok); indirme işi `fetch_company_map`.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

__all__ = ["BIST_COMPANIES_PATH", "CompanyMapError", "fetch_company_map",
           "missing_tickers", "parse_company_map"]

BIST_COMPANIES_PATH = "/tr/bist-sirketler"
BIST_COMPANIES_URL = "https://www.kap.org.tr" + BIST_COMPANIES_PATH

# Ham HTML'de ters bölü ile kaçışlanmış JSON alanları
_OID_PATTERN = re.compile(
    r'\\"mkkMemberOid\\":\\"([^"\\]+)\\",'
    r'\\"kapMemberTitle\\":\\"[^"\\]*\\",'
    r'\\"relatedMemberTitle\\":\\"[^"\\]*\\",'
    r'\\"stockCode\\":\\"([^"\\]*)\\"'
)


class CompanyMapError(RuntimeError):
    """Şirket listesi alınamadı veya beklenen biçimde değil."""


def parse_company_map(html: str) -> dict[str, str]:
    """KAP `bist-sirketler` HTML'inden {ticker: oid} çıkarır.

    Ticker'lar büyük harfe çevrilir; aynı koda iki OID düşerse son kayıt kalır ama
    bu durum pratikte görülmez (kod tekil).
    """
    if not html or "mkkMemberOid" not in html:
        raise CompanyMapError("sayfada mkkMemberOid yok (bot koruması veya biçim değişti)")
    mapping: dict[str, str] = {}
    for oid, codes in _OID_PATTERN.findall(html):
        for code in codes.split(","):
            ticker = code.strip().upper()
            if ticker:
                mapping[ticker] = oid
    if not mapping:
        raise CompanyMapError("eşleme boş: desen sayfayla uyuşmuyor")
    return mapping


def missing_tickers(mapping: Mapping[str, str], wanted: Iterable[str]) -> list[str]:
    """İstenen evrenden eşleşmeyen kodlar (sessizce atlanmasın diye raporlanır)."""
    return [t for t in wanted if t.upper() not in mapping]


async def fetch_company_map(client: object | None = None) -> dict[str, str]:
    """KAP sayfasını indirip eşlemeyi döndürür.

    `client`: `KapClient` benzeri, `get_text(url)` sunan nesne (test enjeksiyonu).
    """
    if client is not None and hasattr(client, "get_text"):
        html = await client.get_text(BIST_COMPANIES_PATH)  # type: ignore[attr-defined]
    else:
        from .kap import KapClient

        async with KapClient() as kap:
            html = await kap.get_text(BIST_COMPANIES_PATH)
    return parse_company_map(html)
