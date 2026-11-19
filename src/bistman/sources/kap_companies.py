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

import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..ticker_map import TickerResolver

__all__ = [
    "BIST_COMPANIES_PATH",
    "CompanyEntry",
    "CompanyMapError",
    "build_company_resolver",
    "fetch_companies",
    "fetch_company_map",
    "missing_tickers",
    "parse_companies",
    "parse_company_map",
    "select_primary_ticker",
]

BIST_COMPANIES_PATH = "/tr/bist-sirketler"
BIST_COMPANIES_URL = "https://www.kap.org.tr" + BIST_COMPANIES_PATH

# Ham HTML'de ters bölü ile kaçışlanmış JSON alanları (`\"` sınırı aşılmaz, `\u0130` korunur)
_COMPANY_PATTERN = re.compile(
    r'\\"mkkMemberOid\\":\\"((?:\\[^"]|[^"\\])+)\\",'
    r'\\"kapMemberTitle\\":\\"((?:\\[^"]|[^"\\])*)\\",'
    r'\\"relatedMemberTitle\\":\\"(?:\\[^"]|[^"\\])*\\",'
    r'\\"stockCode\\":\\"((?:\\[^"]|[^"\\])*)\\"'
)
# Next.js flight parçaları (`self.__next_f.push([1,"..."])`): tek bir şirket kaydı
# iki ayrı `<script>` bloğuna bölünebildiği için önce akış birleştirilir.
_NEXT_FLIGHT_CHUNK = re.compile(
    r'self\.__next_f\.push\(\[\s*1\s*,\s*"((?:\\.|[^"\\])*)"\s*\]\)'
)
_UNESCAPED_COMPANY_PATTERN = re.compile(
    r'"mkkMemberOid":"((?:\\.|[^"\\])+)",'
    r'"kapMemberTitle":"((?:\\.|[^"\\])*)",'
    r'"relatedMemberTitle":"(?:\\.|[^"\\])*",?'
    r'"stockCode":"((?:\\.|[^"\\])*)"'
)

# Çok pay gruplu BIST şirketlerinde ana likit tahta kodu (evren belirtilmediğinde).
_PREFERRED_SHARE_CLASS = frozenset({"ISCTR", "KRDMD"})


@dataclass(frozen=True, slots=True)
class CompanyEntry:
    """KAP şirket listesindeki tek hisse satırı (OID + resmî unvan)."""

    ticker: str
    oid: str
    title: str


class CompanyMapError(RuntimeError):
    """Şirket listesi alınamadı veya beklenen biçimde değil."""


def _unescape_json_str(raw: str) -> str:
    try:
        return str(json.loads(f'"{raw}"'))
    except json.JSONDecodeError:
        return raw


def _collect_matches(
    matches: Iterable[tuple[str, str, str]], out: dict[str, CompanyEntry]
) -> None:
    for raw_oid, raw_title, raw_codes in matches:
        oid = _unescape_json_str(raw_oid).strip()
        title = _unescape_json_str(raw_title).strip()
        for part in _unescape_json_str(raw_codes).split(","):
            ticker = part.strip().upper()
            if ticker and oid:
                out[ticker] = CompanyEntry(ticker=ticker, oid=oid, title=title)


def parse_companies(html: str) -> list[CompanyEntry]:
    """KAP `bist-sirketler` HTML'inden `(ticker, oid, unvan)` listesi çıkarır.

    Canlı `/api/disclosure/list/light` akışında hisse kodu yoktur; hem geçmiş
    sorgusu için OID hem de `TickerResolver` için resmî KAP unvanı aynı sayfadan gelir.
    İki ayrı `self.__next_f.push` parçasına bölünmüş kayıtlar da birleştirilerek çözülür.
    """
    if not html or "mkkMemberOid" not in html:
        raise CompanyMapError("sayfada mkkMemberOid yok (bot koruması veya biçim değişti)")
    by_ticker: dict[str, CompanyEntry] = {}
    _collect_matches(_COMPANY_PATTERN.findall(html), by_ticker)
    chunks = _NEXT_FLIGHT_CHUNK.findall(html)
    if chunks:
        joined_raw = "".join(chunks)
        _collect_matches(_COMPANY_PATTERN.findall(joined_raw), by_ticker)
        decoded = "".join(_unescape_json_str(c) for c in chunks)
        if '"mkkMemberOid":"' in decoded:
            _collect_matches(_UNESCAPED_COMPANY_PATTERN.findall(decoded), by_ticker)
    if not by_ticker:
        raise CompanyMapError("eşleme boş: desen sayfayla uyuşmuyor")
    return list(by_ticker.values())


def parse_company_map(html: str) -> dict[str, str]:
    """KAP `bist-sirketler` HTML'inden {ticker: oid} çıkarır.

    Ticker'lar büyük harfe çevrilir; aynı koda iki OID düşerse son kayıt kalır ama
    bu durum pratikte görülmez (kod tekil).
    """
    return {entry.ticker: entry.oid for entry in parse_companies(html)}


def select_primary_ticker(
    codes: Iterable[str],
    *,
    allowed: set[str] | None = None,
) -> str | None:
    """Aynı `mkkMemberOid`'e ait kodlar içinden birincil BIST pay kodunu seçer.

    Öncelik: (1) izlenen evrendeki kod, (2) çok pay gruplu şirketin ana tahtası
    (`ISCTR`, `KRDMD`), (3) 4–5 harfli standart BIST pay kodu (`GARAN` > `TGB`,
    `HALKB` > `THB`), (4) KAP listesindeki ilk sıra.
    """
    ordered = list(dict.fromkeys(c.strip().upper() for c in codes if c and c.strip()))
    if not ordered:
        return None
    if allowed is not None:
        in_universe = [c for c in ordered if c in allowed]
        if in_universe:
            ordered = in_universe
    best_idx = min(
        range(len(ordered)),
        key=lambda idx: (
            0 if ordered[idx] in _PREFERRED_SHARE_CLASS else 1,
            0 if len(ordered[idx]) in {4, 5} and ordered[idx].isalpha() else 1,
            idx,
        ),
    )
    return ordered[best_idx]


def build_company_resolver(
    entries: Iterable[CompanyEntry],
    *,
    universe: Iterable[str] | None = None,
    preferred: Iterable[str] | None = None,
) -> TickerResolver:
    """`CompanyEntry` listesinden unvan→kod çözümleyici üretir.

    - `universe`: verilirse yalnız bu evrendeki kodlar çözümleyiciye alınır.
    - `preferred`: tüm BIST şirketlerini çözümleyicide tutarken (evren dışı
      şirketleri `dis_evren` olarak tanımak için) çok kodlu şirketlerde
      (`GARAN, TGB`, `ISA, ISB, ISCTR, ISKUR`, `KRDMA, KRDMB, KRDMD`) izlenen
      pay koduna öncelik verir; belirtilmezse ana tahta kodu (`ISCTR`, `KRDMD`,
      `GARAN`) seçilir.
    """
    allowed = {t.strip().upper() for t in universe} if universe is not None else None
    favored = (
        {t.strip().upper() for t in preferred}
        if preferred is not None
        else allowed
    )
    by_oid: dict[str, list[CompanyEntry]] = {}
    for entry in entries:
        if not entry.title:
            continue
        if allowed is not None and entry.ticker not in allowed:
            continue
        by_oid.setdefault(entry.oid, []).append(entry)
    pairs: list[tuple[str, str]] = []
    for group in by_oid.values():
        chosen = select_primary_ticker((e.ticker for e in group), allowed=favored)
        if chosen is None:
            continue
        title = next((e.title for e in group if e.ticker == chosen), group[0].title)
        pairs.append((chosen, title))
    return TickerResolver.from_pairs(pairs)


def missing_tickers(mapping: Mapping[str, str], wanted: Iterable[str]) -> list[str]:
    """İstenen evrenden eşleşmeyen kodlar (sessizce atlanmasın diye raporlanır)."""
    return [t for t in wanted if t.upper() not in mapping]


async def fetch_companies(client: object | None = None) -> list[CompanyEntry]:
    """KAP sayfasını indirip `(ticker, oid, unvan)` listesi döndürür."""
    if client is not None and hasattr(client, "get_text"):
        html = await client.get_text(BIST_COMPANIES_PATH)  # type: ignore[attr-defined]
    else:
        from .kap import KapClient

        async with KapClient() as kap:
            html = await kap.get_text(BIST_COMPANIES_PATH)
    return parse_companies(html)


async def fetch_company_map(client: object | None = None) -> dict[str, str]:
    """KAP sayfasını indirip {ticker: oid} eşlemesini döndürür.

    `client`: `KapClient` benzeri, `get_text(url)` sunan nesne (test enjeksiyonu).
    """
    return {entry.ticker: entry.oid for entry in await fetch_companies(client)}
