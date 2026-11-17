"""KAP'ın üç farklı tarih formatı — hepsi 2026-11-17'de canlı görüldü."""

from datetime import datetime, timezone

import pytest

from bistman.parsing.dates import UnparsableDate, parse_kap_datetime, to_trt


def utc(y, mo, d, hh, mi, ss=0) -> datetime:
    return datetime(y, mo, d, hh, mi, ss, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        # /api/disclosure/list/light  (DD.MM.YYYY HH:MM:SS, TRT)
        ("17.11.2026 15:05:53", utc(2026, 11, 17, 12, 5, 53)),
        # /api/notification/attachment-detail/{idx}  (YYYY.MM.DD HH:MM:SS, TRT)
        ("2026.11.17 14:53:07", utc(2026, 11, 17, 11, 53, 7)),
        # tablo satırı (tarih only -> 00:00 TRT)
        ("05.11.2026", utc(2026, 11, 4, 21, 0, 0)),
        # detaylı sorgu aralık etiketi
        ("17-11-2025", utc(2025, 11, 16, 21, 0, 0)),
    ],
)
def test_formats(raw: str, expected_utc: datetime) -> None:
    assert parse_kap_datetime(raw) == expected_utc


def test_relative_today() -> None:
    now = utc(2026, 11, 17, 12, 0)
    assert parse_kap_datetime("Bugün 14:53", now=now) == utc(2026, 11, 17, 11, 53)


def test_relative_yesterday() -> None:
    now = utc(2026, 11, 17, 12, 0)
    assert parse_kap_datetime("Dün 09:10", now=now) == utc(2026, 11, 16, 6, 10)


def test_unparsable_raises_not_silent() -> None:
    """Sessiz None dönmek, sessiz ölümün kaynağı — format tanınmıyorsa patla."""
    with pytest.raises(UnparsableDate):
        parse_kap_datetime("17/11/2026 15:05")
    with pytest.raises(UnparsableDate):
        parse_kap_datetime("")


def test_roundtrip_to_trt() -> None:
    dt = parse_kap_datetime("2026.11.17 14:53:07")
    assert to_trt(dt).strftime("%d.%m.%Y %H:%M:%S") == "17.11.2026 14:53:07"
