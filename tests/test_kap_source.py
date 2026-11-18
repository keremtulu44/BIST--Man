"""KAP firehose testleri — ağ YOK, gerçek fixture + httpx.MockTransport."""

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from bistman.sources.kap import (
    KapClient,
    KapError,
    KapParseError,
    KapUnavailable,
    parse_detail,
    parse_light,
)

FIXTURES = Path(__file__).parent / "fixtures" / "kap"
LIGHT = (FIXTURES / "list_light_sample.json").read_bytes()
DETAIL = (FIXTURES / "detail_1699030.json").read_bytes()


def utc(y, mo, d, hh, mi, ss=0) -> datetime:
    return datetime(y, mo, d, hh, mi, ss, tzinfo=timezone.utc)


# ------------------------------------------------------------------ parse


def test_parse_light_counts_and_order() -> None:
    result = parse_light(LIGHT)
    assert len(result) == 9
    assert result.rejected == []
    # akış yeniden eskiye
    assert result.items[0].index == 1699035
    assert result.items[-1].index == 1699002


def test_parse_light_dates_are_utc() -> None:
    """TRT 15:01:34 -> UTC 12:01:34. Bu, event study'nin t0 seçimini belirler."""
    first = parse_light(LIGHT).items[0]
    assert first.published_at == utc(2026, 11, 17, 12, 1, 34)
    assert first.published_at.tzinfo is timezone.utc


def test_parse_light_keeps_null_summary_as_none() -> None:
    items = parse_light(LIGHT).items
    gon = next(i for i in items if i.subject == "Şirket Genel Bilgi Formu")
    assert gon.summary is None


def test_parse_light_has_no_tickers() -> None:
    """light ucu ticker vermiyor: unvan->kod eşlemesi zorunlu (tasarım sonucu)."""
    assert all(i.tickers == () for i in parse_light(LIGHT).items)


def test_parse_light_bad_row_does_not_kill_batch() -> None:
    payload = b'[{"publishDate":"17.11.2026 15:00:00","disclosureIndex":1,"title":"X","subject":"A"},' \
              b'{"publishDate":"BOZUK","disclosureIndex":2,"title":"Y","subject":"B"}]'
    result = parse_light(payload)
    assert len(result) == 1
    assert result.rejected and result.rejected[0][0] == 1


@pytest.mark.parametrize("raw", [b"", b"null", b"{}", b"<html>WAF</html>"])
def test_parse_light_fails_loudly(raw: bytes) -> None:
    """Sessizce boş liste dönmek 'haber yok' sanılır — bu yüzden patlıyoruz."""
    with pytest.raises(KapParseError):
        parse_light(raw)


def test_empty_feed_is_empty_result_not_error() -> None:
    """KAP gerçekten boş dizi dönebilir; 'şüpheli' kararı çağıran katmanın."""
    assert len(parse_light(b"[]")) == 0


def test_parse_detail_structural_ticker() -> None:
    detail = parse_detail(DETAIL)
    assert detail.stub.tickers == ("MTRKS",)  # light'ta yoktu, burada var
    assert detail.stub.disclosure_class == "ODA"
    assert detail.stub.member_oid == "8acae2c575a1b5b60177de29c7177130"
    assert detail.stub.published_at == utc(2026, 11, 17, 11, 53, 7)
    assert detail.attachment_count == 0
    assert "disclosureBody" not in detail.body_html and detail.body_html


def test_parse_detail_empty_array_raises() -> None:
    with pytest.raises(KapParseError):
        parse_detail(b"[]")


# ----------------------------------------------------------------- client


def _client(handler, **kw) -> KapClient:
    kw.setdefault("sleep", lambda _s: asyncio.sleep(0))
    return KapClient(transport=httpx.MockTransport(handler), **kw)


def test_client_light_ok() -> None:
    async def run() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, content=LIGHT)

        async with _client(handler) as kap:
            result = await kap.light()
        assert len(result) == 9
        assert calls and calls[0].endswith("/tr/api/disclosure/list/light")

    asyncio.run(run())


def test_client_retries_on_429_and_honours_retry_after() -> None:
    async def run() -> None:
        slept: list[float] = []
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return httpx.Response(200, content=LIGHT)

        kap = _client(handler, sleep=lambda s: slept.append(s) or asyncio.sleep(0))
        result = await kap.light()
        await kap.aclose()

        assert len(result) == 9
        assert attempts["n"] == 2
        assert slept == [7.0]  # Retry-After'a uyuldu

    asyncio.run(run())


def test_client_gives_up_after_max_attempts() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        kap = _client(handler, max_attempts=3)
        with pytest.raises(KapUnavailable):
            await kap.light()
        await kap.aclose()

    asyncio.run(run())


def test_client_does_not_retry_403() -> None:
    async def run() -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(403)

        kap = _client(handler)
        with pytest.raises(KapError):
            await kap.light()
        await kap.aclose()
        assert attempts["n"] == 1  # retry yok

    asyncio.run(run())


def test_client_sends_identifying_user_agent() -> None:
    async def run() -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, content=b"[]")

        async with _client(handler) as kap:
            await kap.light()
        assert "bistman" in seen["ua"]

    asyncio.run(run())


# --------------------------------------------------- gövde: düz metin (body_provider)


def test_fetch_body_returns_turkish_only_text() -> None:
    """`fetch_body` pipeline'a HAZIR metin vermeli: İngilizce kopya ve GWT işaretleri yok."""
    detail_bytes = (FIXTURES / "detail_1699030_live.json").read_bytes()

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "attachment-detail/1699030" in str(request.url)
            return httpx.Response(200, content=detail_bytes)

        async with _client(handler) as kap:
            text, detail = await kap.fetch_body(1699030)

        assert "Haber veya Söylentilere İlişkin Açıklama" in text
        assert "Notification About News Or Rumours" not in text
        assert "oda_ExplanationTextBlock|" not in text
        assert len(text) < 6000  # ham HTML 22 KB; TR-only ~4 KB
        assert detail.stub.tickers == ("MTRKS",)

    asyncio.run(run())


def test_fetch_history_parses_real_fixture() -> None:
    """Backfill birimi: tek istek -> 1 yıl -> ayrıştırılmış satırlar."""
    history_html = (FIXTURES / "member_history_mtrks.html").read_bytes()

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "bildirim-sorgu-sonuc?member=" in str(request.url)
            return httpx.Response(200, content=history_html)

        async with _client(handler) as kap:
            rows = await kap.fetch_history("8acae2c575a1b5b60177de29c7177130", today=date(2026, 11, 17))

        assert len(rows) == 89
        assert rows[0].index == 1699030
        assert {r.disclosure_class for r in rows} == {"öda", "fr", "dkb", "dg"}

    asyncio.run(run())


def test_fetch_body_propagates_parse_error() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'[{"disclosure": {}}]')

        async with _client(handler) as kap:
            with pytest.raises(KapParseError):
                await kap.fetch_body(1)

    asyncio.run(run())


def test_foreign_detail_never_reaches_body_or_cache() -> None:
    async def run():
        async with _client(lambda _: httpx.Response(200, content=DETAIL)) as kap:
            with pytest.raises(KapParseError, match="1699030"):
                await kap.fetch_body(12345)

    asyncio.run(run())


@pytest.mark.parametrize("field", ["subject", "summary", "title", "disclosureClass"])
def test_light_rejects_one_nontext_field_without_poisoning_the_batch(field) -> None:
    import json

    rows = json.loads(LIGHT)[:2]
    rows[0][field] = {"beklenmeyen": "şema"}
    result = parse_light(json.dumps(rows))
    assert len(result.items) == len(result.rejected) == 1
    assert result.rejected[0][0] == 0 and field in result.rejected[0][1]


def test_detail_body_schema_drift_is_not_joined_character_by_character() -> None:
    import json

    [entry] = json.loads(DETAIL)
    entry["disclosureBody"] = "60 milyon TL"
    with pytest.raises(KapParseError, match="disclosureBody"):
        parse_detail(json.dumps([entry]))
