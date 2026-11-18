"""İş Yatırım `MaliTablo` istemcisi — **standart kütüphane**, bağımlılık yok.

Neden borsapy değil: `pip install borsapy` kullanıcının makinesinde takıldı
(pandas/numpy/httpx tekerlekleri) ve zaten iki kritik şeyi yanlış yapıyordu —
TTM'i 2x fazla sayıyor, banka şablonunda alt kalem seçiyordu. Geriye kalan tek
değeri bu ucu çağırmaktı; o da 40 satır.

Uç (borsapy kaynağından birebir okundu ve doğrulandı):

    GET https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common
        /Data.aspx/MaliTablo
        ?companyCode=THYAO&exchange=TRY&financialGroup=XI_29
        &year1=2026&period1=6&year2=2026&period2=3&year3=2025&period3=12&year4=2025&period4=9

    -> {"value": [{"itemDescTr": "Satış Gelirleri", "itemCode": "3C",
                   "value1": 585069000000, "value2": 257961000000, ...}, ...]}

Kurallar (gerçek yanıtlarda doğrulandı):
  * `period` = AY (3/6/9/12); çeyrek = ay // 3
  * tek istekte en fazla **4 dönem** -> 8 çeyrek = 2 istek
  * `financialGroup`: XI_29 = sanayi, UFRS = banka
  * yanıt **üç tabloyu birden** içerir (1xxx bilanço-aktif, 2xxx pasif,
    3xxx gelir, 4xxx nakit akışı) -> tablo ayrımı `isyatirim_labels`'ta
  * değerler **kümülatif (YTD)** -> TTM `analysis/ttm`'de hesaplanır

Senkron `urllib` çağrısı `asyncio.to_thread` ile köprülenir; olay döngüsü bloke
olmaz (KAP istemcisinin aksine burada HTTP/2, retry-after gibi ihtiyaçlar yok).
"""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from .base import FundamentalRow
from .isyatirim_labels import fundamentals_from_items

__all__ = [
    "FINANCIAL_GROUP_BANK",
    "FINANCIAL_GROUP_INDUSTRIAL",
    "IsYatirimClient",
    "IsYatirimError",
    "Item",
    "quarters_back",
    "years_back",
]

BASE_URL = (
    "https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
}
FINANCIAL_GROUP_INDUSTRIAL = "XI_29"
FINANCIAL_GROUP_BANK = "UFRS"
MAX_PERIODS_PER_CALL = 4


class IsYatirimError(RuntimeError):
    """Veri alınamadı / beklenmeyen yanıt."""


@dataclass(frozen=True, slots=True)
class Item:
    """Tek finansal tablo kalemi."""

    label: str
    code: str
    values: dict[tuple[int, int], float | None]

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "code": self.code, "values": dict(self.values)}


def quarters_back(count: int = 8, today: date | None = None) -> list[tuple[int, int]]:
    """Yayın gecikmesini hesaba katan çeyrek listesi (dönem = ay).

    Ocak-Şubat: geçen yıl Q3 · Mart-Mayıs: geçen yıl Q4 · Haziran-Ağustos: bu yıl Q1
    Eylül-Kasım: bu yıl Q2 · Aralık: bu yıl Q3
    """
    today = today or date.today()
    if today.month <= 2:
        year, month = today.year - 1, 9
    elif today.month <= 5:
        year, month = today.year - 1, 12
    elif today.month <= 8:
        year, month = today.year, 3
    elif today.month <= 11:
        year, month = today.year, 6
    else:
        year, month = today.year, 9

    out: list[tuple[int, int]] = []
    for _ in range(count):
        out.append((year, month))
        month -= 3
        if month <= 0:
            month, year = 12, year - 1
    return out


def years_back(count: int = 4, today: date | None = None) -> list[tuple[int, int]]:
    today = today or date.today()
    return [(today.year - 1 - i, 12) for i in range(count)]


class IsYatirimClient:
    """Senkron çekirdek + asenkron yüzey. Testte `fetcher` enjekte edilebilir."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        allow_insecure_ssl: bool = False,
        fetcher: Any | None = None,
    ) -> None:
        self._timeout = timeout
        self._allow_insecure = allow_insecure_ssl
        self._fetcher = fetcher  # test: (url) -> bytes

    # ------------------------------------------------------------ HTTP

    def _http_get(self, url: str) -> bytes:
        if self._fetcher is not None:
            return self._fetcher(url)

        context = ssl.create_default_context()
        try:
            return self._read(url, context)
        except urllib.error.URLError as exc:
            cert_problem = isinstance(exc.reason, ssl.SSLCertVerificationError) or (
                "CERTIFICATE_VERIFY_FAILED" in str(exc.reason).upper()
            )
            if not (cert_problem and self._allow_insecure):
                raise IsYatirimError(f"bağlantı hatası: {exc.reason}") from exc
            # Geriye uyumluluk: yalnız çağıran açıkça izin verdiyse.
            warnings.warn("Mali tablo TLS doğrulaması olmadan alınıyor", RuntimeWarning, stacklevel=2)
            return self._read(url, ssl._create_unverified_context())

    def _read(self, url: str, context: ssl.SSLContext) -> bytes:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=self._timeout, context=context) as response:
            return response.read()

    # ------------------------------------------------------------ veri

    def _fetch_chunk_sync(
        self, ticker: str, group: str, chunk: Sequence[tuple[int, int]]
    ) -> list[Item]:
        params: dict[str, str] = {
            "companyCode": ticker.upper(),
            "exchange": "TRY",
            "financialGroup": group,
        }
        for i, (year, month) in enumerate(chunk, 1):
            params[f"year{i}"] = str(year)
            params[f"period{i}"] = str(month)
        url = BASE_URL + "?" + urllib.parse.urlencode(params)

        try:
            raw = self._http_get(url)
        except urllib.error.HTTPError as exc:
            raise IsYatirimError(f"HTTP {exc.code} ({ticker})") from exc

        text = raw.decode("utf-8", "replace").lstrip("\ufeff")
        if not text.strip().startswith("{"):
            raise IsYatirimError(f"JSON değil ({ticker}): {text[:120]!r}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IsYatirimError(f"JSON çözümlenemedi ({ticker}): {exc}") from exc

        rows = payload.get("value") or []
        if not rows:
            raise IsYatirimError(f"boş yanıt ({ticker}, dönem {list(chunk)})")

        items: list[Item] = []
        for row in rows:
            values: dict[tuple[int, int], float | None] = {}
            for i, period in enumerate(chunk, 1):
                raw_value = row.get(f"value{i}")
                try:
                    values[period] = None if raw_value is None else float(raw_value)
                except (TypeError, ValueError):
                    values[period] = None
            items.append(
                Item(
                    label=str(row.get("itemDescTr") or row.get("itemDescEng") or ""),
                    code=str(row.get("itemCode") or ""),
                    values=values,
                )
            )
        return items

    async def fetch_items(
        self,
        ticker: str,
        *,
        statement_type: str = "industrial",
        periods: Sequence[tuple[int, int]] | None = None,
    ) -> list[Item]:
        """Dönemleri 4'lük parçalarla çeker, kalemleri etikete göre birleştirir."""
        group = FINANCIAL_GROUP_BANK if statement_type == "bank" else FINANCIAL_GROUP_INDUSTRIAL
        chunks = [
            list(periods[i : i + MAX_PERIODS_PER_CALL])
            for i in range(0, len(periods or []), MAX_PERIODS_PER_CALL)
        ]
        merged: dict[str, Item] = {}
        for chunk in chunks:
            items = await asyncio.to_thread(self._fetch_chunk_sync, ticker, group, chunk)
            for item in items:
                if item.label in merged:
                    existing = merged[item.label]
                    merged[item.label] = Item(
                        label=existing.label,
                        code=existing.code or item.code,
                        values={**existing.values, **item.values},
                    )
                else:
                    merged[item.label] = item
        return list(merged.values())

    async def fundamentals(
        self,
        ticker: str,
        *,
        statement_type: str = "industrial",
        market_cap: float | None = None,
        today: date | None = None,
    ) -> FundamentalRow:
        """8 çeyrek + 4 yıl -> `FundamentalRow` (kümülatif düzeltmeli TTM)."""
        quarterly = await self.fetch_items(
            ticker, statement_type=statement_type, periods=quarters_back(8, today)
        )
        annual = await self.fetch_items(
            ticker, statement_type=statement_type, periods=years_back(4, today)
        )
        latest = max((p for item in quarterly for p in item.values), default=None)
        period_end = f"{latest[0]}-{latest[1]:02d}-01" if latest else ""

        return fundamentals_from_items(
            ticker=ticker.upper(),
            quarterly=[item.as_dict() for item in quarterly],
            annual=[item.as_dict() for item in annual],
            statement_type=statement_type,
            market_cap=market_cap,
            period_end=period_end,
        )
