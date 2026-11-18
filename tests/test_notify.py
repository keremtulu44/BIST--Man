"""Bildirim katmanı testleri — Telegram çağrısı mock transport ile, ağ yok."""

import asyncio

import httpx
import pytest

from bistman.notify import FakeNotifier, TelegramNotifier, chunk_message


# ------------------------------------------------------------------ parçalama


def test_short_message_is_single_chunk() -> None:
    assert chunk_message("kısa") == ["kısa"]


def test_long_message_splits_on_line_boundaries() -> None:
    text = "\n".join(["x" * 100] * 100)  # ~10.100 karakter
    chunks = chunk_message(text, limit=4000)
    assert len(chunks) >= 3
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_fake_notifier_records_and_can_fail() -> None:
    async def run() -> None:
        ok = FakeNotifier()
        assert await ok.send("a", key="k1") is True
        assert ok.count == 1 and ok.sent[0] == ("k1", "a")

        bad = FakeNotifier(fail=True)
        assert await bad.send("a", key="k1") is False
        assert bad.count == 0

    asyncio.run(run())


# ------------------------------------------------------------------- telegram


def _notifier(handler, **kw) -> TelegramNotifier:
    kw.setdefault("sleep", lambda _s: asyncio.sleep(0))
    return TelegramNotifier(
        token="TEST", chat_id=42, transport=httpx.MockTransport(handler), **kw
    )


def test_telegram_success_payload() -> None:
    async def run() -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return httpx.Response(200, json={"ok": True, "result": {}})

        notifier = _notifier(handler)
        assert await notifier.send("merhaba", key="k") is True
        await notifier.aclose()

        assert "/botTEST/sendMessage" in str(seen["url"])
        body = seen["body"]
        assert b'"chat_id"' in body and b"42" in body
        # MarkdownV2 kaçırma derdi olmasın: parse_mode gönderilmiyor
        assert b"parse_mode" not in body

    asyncio.run(run())


def test_telegram_400_is_terminal_not_retried() -> None:
    async def run() -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(400, json={"ok": False, "description": "bad chat_id"})

        notifier = _notifier(handler)
        assert await notifier.send("x", key="k") is False
        await notifier.aclose()
        assert attempts["n"] == 1

    asyncio.run(run())


def test_telegram_429_waits_retry_after_then_succeeds() -> None:
    async def run() -> None:
        slept: list[float] = []
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(
                    429, json={"ok": False, "parameters": {"retry_after": 5}}
                )
            return httpx.Response(200, json={"ok": True})

        notifier = _notifier(handler, sleep=lambda s: slept.append(s) or asyncio.sleep(0))
        assert await notifier.send("x", key="k") is True
        await notifier.aclose()
        assert attempts["n"] == 2
        assert slept == [5.0]

    asyncio.run(run())


def test_telegram_transport_error_retries_then_fails() -> None:
    async def run() -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ConnectError("boom", request=request)

        notifier = _notifier(handler, max_attempts=3)
        assert await notifier.send("x", key="k") is False
        await notifier.aclose()
        assert attempts["n"] == 3

    asyncio.run(run())


def test_empty_token_rejected_early() -> None:
    with pytest.raises(ValueError):
        TelegramNotifier(token="", chat_id=1)


def test_chunks_preserve_long_line_including_final_qualification() -> None:
    text = "Haber\r\n" + "Ş📌" * 4500 + " İPTAL.\nSon kayıt."
    chunks = chunk_message(text)
    assert "".join(chunks) == text
    assert all(0 < len(part.encode("utf-16-le")) // 2 <= 4000 for part in chunks)


@pytest.mark.parametrize("content", [b'{"ok":false}', b'{}', b'{"ok":"true"}', b'[]', b'<html>proxy</html>'])
def test_telegram_requires_positive_api_ack_and_stops_on_failed_chunk(content) -> None:
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(200, json={"ok": True}) if len(attempts) == 1 else httpx.Response(200, content=content)

    async def run():
        notifier = _notifier(handler)
        try:
            assert not await notifier.send(("x" * 3999 + "\n") * 3, key="k")
        finally:
            await notifier.aclose()

    asyncio.run(run())
    assert len(attempts) == 2  # ilk parça gitmiş olabilir; kopuk üçüncü parça gitmesin


def test_lost_response_after_post_is_not_reposted_blindly() -> None:
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("yanıt alınamadı", request=request)

    async def run():
        notifier = _notifier(handler)
        try:
            assert not await notifier.send("haber", key="k")
        finally:
            await notifier.aclose()

    asyncio.run(run())
    assert len(attempts) == 1
