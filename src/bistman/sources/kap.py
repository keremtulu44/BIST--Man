"""KAP firehose istemcisi — piyasa geneli bildirim akışı (borsapy'de YOK).

Doğrulanmış uçlar (2026-11-17 canlı test):

    GET /tr/api/disclosure/list/light                  -> bugünün tüm akışı (JSON)
    GET /tr/api/notification/attachment-detail/{idx}   -> tek bildirim + yapısal ticker
    GET /tr/bildirim-sorgu-sonuc?member={oid}          -> üye bazlı geçmiş (1 yıl, HTML)

Neden bu modül var: `borsapy.Ticker.news` **hisse bazlı ve son 20 kayıt**; 600 hisseyi
tarayarak akış kurmak imkânsız. Ayrıca o fonksiyon regex'e dayalı ve şablon
değiştiğinde **boş DataFrame** döner (exception yok). Firehose bunun yerine
JSON ayrıştırır ve şüpheli durumda **patlar**.

Tasarım kuralları:
  * Boş/ayrıştırılamayan yanıt -> `KapParseError` (sessizce boş liste YOK).
  * Tek satır bozuksa diğerleri kurtarılır ama `ParseResult.rejected`'a yazılır.
  * 429/5xx/transport: üstel backoff + jitter; `Retry-After`'a uyulur.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx

from ..parsing.dates import UnparsableDate, parse_kap_datetime
from .base import DisclosureDetail, DisclosureStub, ParseResult

BASE_URL = "https://www.kap.org.tr"
LIGHT_PATH = "/tr/api/disclosure/list/light"
DETAIL_PATH = "/tr/api/notification/attachment-detail/{index}"
MEMBER_HISTORY_PATH = "/tr/bildirim-sorgu-sonuc"

# Kendini tanıtan UA: KAP WAF'ı boş/otomatik UA'ları kesiyor (gözlem)
USER_AGENT: str = (
    "bistman/0.1 (kisisel arastirma; +https://github.com/keremtulu44/BIST--Man)"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Referer": f"{BASE_URL}/tr/bildirim-sorgu",
}


class KapError(RuntimeError):
    """KAP katmanı hatalarının taban sınıfı."""


class KapParseError(KapError):
    """Yanıt beklenen şekilde değil. Sessizce boş dönmek yerine fırlatılır."""


class KapUnavailable(KapError):
    """Retry bütçesi tükendi (429/5xx/transport)."""


# ---------------------------------------------------------------- ayrıştırma


def _require_list(payload: Any, what: str) -> list[Any]:
    if not isinstance(payload, list):
        raise KapParseError(f"{what}: JSON dizi bekleniyordu, {type(payload).__name__} geldi")
    return payload


def _tickers_from(raw: Any) -> tuple[str, ...]:
    """`stockCode` / `relatedStocks` -> normalize edilmiş ticker demeti.

    `relatedStocks` çok hisseli bildirimlerde "IDH, IEV, IJB" gibi gelir;
    tek hisselide `null` olup `stockCode` doludur.
    """
    codes: list[str] = []
    for chunk in (raw,) if isinstance(raw, str) else (raw or []):
        if not isinstance(chunk, str):
            continue
        for part in chunk.replace(";", ",").split(","):
            code = part.strip().upper()
            # KAP kodları 4-5 karakter (THYAO, MTRKS, ISMEN); sayı/boşluk ele
            if 3 <= len(code) <= 6 and code.isalnum() and not code.isdigit():
                codes.append(code)
    return tuple(dict.fromkeys(codes))


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key}: metin bekleniyordu")
    return value.strip()


def parse_light(raw: bytes | str) -> ParseResult:
    """`/api/disclosure/list/light` yanıtını `DisclosureStub` listesine çevirir."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KapParseError(f"light: JSON çözümlenemedi: {exc}") from exc

    rows = _require_list(payload, "light")
    items: list[DisclosureStub] = []
    rejected: list[tuple[int, str]] = []

    for pos, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected.append((pos, "satır dict değil"))
            continue
        try:
            index = int(row["disclosureIndex"])
            subject = _text(row, "subject")
            if not subject:
                raise ValueError("subject boş")
            items.append(
                DisclosureStub(
                    index=index,
                    published_at=parse_kap_datetime(str(row["publishDate"])),
                    title=_text(row, "title"),
                    subject=subject,
                    summary=_text(row, "summary") or None,
                    # light ucunda sınıf/ticker YOK — bu yüzden .get() ile boş bırakıyoruz
                    disclosure_class=_text(row, "disclosureClass") or None,
                )
            )
        except (KeyError, TypeError, ValueError, UnparsableDate) as exc:
            rejected.append((pos, f"{type(exc).__name__}: {exc}"))

    return ParseResult(items=items, rejected=rejected)


def parse_detail(raw: bytes | str) -> DisclosureDetail:
    """Detay ucu yanıtını `DisclosureDetail`'e çevirir (yapısal ticker burada)."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KapParseError(f"detail: JSON çözümlenemedi: {exc}") from exc

    rows = _require_list(payload, "detail")
    if not rows:
        raise KapParseError("detail: boş dizi (bildirim bulunamadı)")

    entry = rows[0]
    try:
        basic = entry["disclosure"]["disclosureBasic"]
        index = int(basic["disclosureIndex"])
        published_at = parse_kap_datetime(str(basic["publishDate"]))
    except (KeyError, TypeError, ValueError, UnparsableDate) as exc:
        raise KapParseError(f"detail: beklenen alanlar yok: {exc}") from exc

    tickers = _tickers_from(basic.get("stockCode"))
    if not tickers:
        tickers = _tickers_from(basic.get("relatedStocks"))

    body_parts = entry.get("disclosureBody")
    if body_parts is None:
        body_parts = []
    if not isinstance(body_parts, list) or not all(isinstance(p, str) for p in body_parts):
        raise KapParseError("detail: disclosureBody metin dizisi değil")
    body_html = "\n".join(p for p in body_parts if p)

    stub = DisclosureStub(
        index=index,
        published_at=published_at,
        title=(basic.get("companyTitle") or "").strip(),
        subject=(basic.get("title") or "").strip(),
        summary=basic.get("summary") or None,
        disclosure_class=basic.get("disclosureClass") or None,
        tickers=tickers,
        member_oid=basic.get("mkkMemberOid") or None,
    )
    return DisclosureDetail(
        stub=stub,
        body_html=body_html,
        attachment_count=int(basic.get("attachmentCount") or 0),
        is_late=bool(basic.get("isLate") or False),
    )


# ---------------------------------------------------------------- istemci


class KapClient:
    """KAP uçları için asenkron istemci. Testlerde `transport` enjekte edilir."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        base_url: str = BASE_URL,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers=DEFAULT_HEADERS,
            transport=transport,
            follow_redirects=True,
        )
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._base = base_url

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> KapClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, path: str, *, referer: str | None = None) -> httpx.Response:
        url = f"{self._base}{path}"
        headers = {"Referer": referer} if referer else None
        last_exc: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.TransportError as exc:  # timeout, DNS, bağlantı
                last_exc = exc
            else:
                if response.status_code == 200:
                    return response
                if response.status_code == 429 or response.status_code >= 500:
                    last_exc = KapUnavailable(f"HTTP {response.status_code}: {url}")
                    retry_after = _retry_after_seconds(response)
                    if retry_after is not None:
                        await self._sleep(retry_after)
                        continue
                else:
                    # 4xx (403/404): retry anlamsız
                    raise KapError(f"HTTP {response.status_code}: {url}")

            # üstel backoff + jitter (rate limit cezasına yakalanmamak için)
            await self._sleep(min(2 ** (attempt - 1), 30) + random.random())

        raise KapUnavailable(f"{self._max_attempts} deneme başarısız: {url}") from last_exc

    async def get_text(self, path: str) -> str:
        """Ham metin/HTML yanıtı — JSON olmayan uçlar için (şirket listesi vb.)."""
        response = await self._get(path)
        return response.text

    async def light(self) -> ParseResult:
        """Bugünün tüm bildirim akışı. Boş sonuç **şüpheli**dir (çağıran kontrol eder)."""
        response = await self._get(LIGHT_PATH)
        return parse_light(response.content)

    async def detail(self, index: int) -> DisclosureDetail:
        """Tek bildirim detayı — yapısal `stockCode` ve gövde için."""
        response = await self._get(
            DETAIL_PATH.format(index=index),
            referer=f"{self._base}/tr/Bildirim/{index}",
        )
        detail = parse_detail(response.content)
        if detail.stub.index != index:
            raise KapParseError(f"detail: istenen bildirim {index}, gelen {detail.stub.index}")
        return detail

    async def member_history_raw(self, member_oid: str) -> str:
        """Üye bazlı geçmiş (varsayılan 1 yıl) — HAM HTML.

        Ayrıştırma `parsing.kap_table.parse_history`'de; bu metot sadece indirir.
        Tek istek = 1 yıllık tam geçmiş (17.11.2026 doğrulandı: 89 bildirim).
        """
        response = await self._get(f"{MEMBER_HISTORY_PATH}?member={member_oid}")
        return response.text

    async def fetch_history(self, member_oid: str, *, today: date | None = None) -> list:
        """Üye geçmişini ayrıştırılmış satırlar olarak döndürür (backfill birimi)."""
        from ..parsing.kap_table import parse_history

        raw = await self.member_history_raw(member_oid)
        return parse_history(raw, today=today)

    async def fetch_body(self, index: int) -> tuple[str, DisclosureDetail]:
        """Bildirim gövdesini **düz metin** olarak indirir (pipeline `body_provider`'ı).

        Gövde HTML'i TR + EN iki kopya içeriyor; düz metne çevrilirken İngilizce
        kopya atılır (aynı tutarın iki kez görünmemesi için). Ağ maliyeti:
        bildirim başına 1 istek — yalnız adaylar için çağrılmalı.
        """
        from ..parsing.kap_table import body_text

        detail = await self.detail(index)
        return body_text(detail.body_html), detail


MAX_RETRY_AFTER = 60.0


def _retry_after_seconds(response: httpx.Response, *, cap: float = MAX_RETRY_AFTER) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return min(cap, max(0.0, value))
