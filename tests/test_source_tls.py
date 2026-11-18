"""Finansal veride sertifika hatası otomatik olarak atlanmamalı (ağ yok)."""

import asyncio
import ssl
import urllib.error
import urllib.request

import pytest

from bistman.sources.isyatirim_client import IsYatirimClient, IsYatirimError
from bistman.sources.prices import PriceError, PriceSource, fetch_series


@pytest.mark.parametrize("source", ["fundamentals", "prices"])
def test_tls_verification_failure_is_terminal_by_default(source, monkeypatch) -> None:
    attempts = []

    def reject(request, *, timeout, context):
        attempts.append((context.verify_mode, context.check_hostname))
        raise urllib.error.URLError(ssl.SSLCertVerificationError("sertifika doğrulanamadı"))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    if source == "fundamentals":
        with pytest.raises(IsYatirimError, match="sertifika"):
            asyncio.run(IsYatirimClient().fetch_items("THYAO", periods=[(2026, 6)]))
    else:
        with pytest.raises(PriceError, match="sertifika"):
            fetch_series("THYAO", PriceSource("stock"))
    assert attempts == [(ssl.CERT_REQUIRED, True)]
