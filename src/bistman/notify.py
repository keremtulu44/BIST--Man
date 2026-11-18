"""Bildirim katmanı — Telegram (v1) ve test için FakeNotifier.

Telegram tuzakları (MIMARI.md §5):
  * MarkdownV2'de `_ * [ ] ( ) ~ ` > # + - = | { } . !` kaçırılmazsa API 400
    döner ve Türkçe metinlerde ("A.Ş.", "%3,0") sürekli kırılır. Bu yüzden
    v1'de **parse_mode kullanılmıyor**: düz metin, kaçırma derdi yok.
  * Mesaj sınırı 4096 → 4000'de bölünür.
  * 429'da `retry_after` beklenir; 400/403'te retry YOK (kalıcı hata).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

import httpx

TELEGRAM_API = "https://api.telegram.org"
MAX_CHUNK = 4000


@dataclass
class FakeNotifier:
    """Test/replay için: hiçbir yere göndermez, gönderilenleri kaydeder."""

    sent: list[tuple[str, str]] = field(default_factory=list)
    fail: bool = False

    async def send(self, message: str, *, key: str) -> bool:
        if self.fail:
            return False
        self.sent.append((key, message))
        return True

    @property
    def count(self) -> int:
        return len(self.sent)


def chunk_message(text: str, limit: int = MAX_CHUNK) -> list[str]:
    """Uzun mesajı satır sınırlarından bölerek parçalara ayırır."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)
            current = line[:limit]
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


@dataclass
class TelegramNotifier:
    token: str
    chat_id: str | int
    transport: httpx.AsyncBaseTransport | None = None
    timeout: float = 10.0
    max_attempts: int = 3
    sleep: Callable[[float], object] = asyncio.sleep
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("Telegram token boş")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, message: str, *, key: str) -> bool:
        ok = True
        for chunk in chunk_message(message):
            ok = await self._send_chunk(chunk) and ok
        return ok

    async def _send_chunk(self, text: str) -> bool:
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.client.post(url, json=payload)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    return False
                await self.sleep(min(2 ** (attempt - 1), 10))
                continue

            if response.status_code == 200:
                return True
            if response.status_code == 429:
                retry_after = _retry_after(response)
                if attempt == self.max_attempts:
                    return False
                await self.sleep(retry_after if retry_after is not None else 2 ** attempt)
                continue
            # 400/403: token/chat_id/mesaj hatası — retry anlamsız
            return False
        return False


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.json().get("parameters", {}).get("retry_after"))
    except (ValueError, AttributeError, TypeError):
        return None
