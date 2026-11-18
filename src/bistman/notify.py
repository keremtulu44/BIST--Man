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
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

TELEGRAM_API = "https://api.telegram.org"
MAX_CHUNK = 4000


class Notifier(Protocol):
    async def send(self, message: str, *, key: str) -> bool: ...


async def send_checked(notifier: Notifier, message: str, *, key: str) -> bool:
    """Yan etki sınırı: bir başarısız mesaj diğerlerini engellemez. İptal yayılır."""
    try:
        return await notifier.send(message, key=key) is True
    except Exception as exc:
        # URL/token veya haber metnini loglama.
        logging.getLogger(__name__).warning("Gönderim onaylanmadı: %s (%s)", key, type(exc).__name__)
        return False


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
    """Önce satır sonundan, uzun tek satırda karakterden böl. Hiçbir eki atma."""
    if limit <= 0:
        raise ValueError("parça sınırı pozitif olmalı")
    if not text:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start
        newline = start
        units = 0
        while end < len(text):
            # UTF-16 birimiyle ihtiyatlı sınır: emoji ortadan bölünmez.
            units += 2 if ord(text[end]) > 0xFFFF else 1
            if units > limit:
                break
            end += 1
            if text[end - 1] == "\n":
                newline = end
        if end == start:
            raise ValueError("tek karakter parça sınırını aşıyor")
        if end < len(text) and newline > start:
            end = newline
        chunks.append(text[start:end])
        start = end
    return chunks


@dataclass
class TelegramNotifier:
    token: str
    chat_id: str | int
    transport: httpx.AsyncBaseTransport | None = None
    timeout: float = 10.0
    max_attempts: int = 3
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
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
        for chunk in chunk_message(message):
            if not await self._send_chunk(chunk):
                return False  # daha önceki parçalar gitmiş olabilir; sonrasını yollama
        return True

    async def _send_chunk(self, text: str) -> bool:
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.client.post(url, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                if attempt == self.max_attempts:
                    return False
                await self.sleep(min(2 ** (attempt - 1), 10))
                continue
            except httpx.TransportError:
                # POST yazıldıktan sonra yanıt kaybolmuş olabilir: tekrar = çift mesaj riski.
                return False

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    return False
                return isinstance(data, dict) and data.get("ok") is True
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
        seconds = float(response.json().get("parameters", {}).get("retry_after"))
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (ValueError, AttributeError, TypeError):
        return None
