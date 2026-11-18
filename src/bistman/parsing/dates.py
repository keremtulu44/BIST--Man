"""KAP tarih formatlarının tek normalizasyon noktası.

2026-11-17'de canlı denemede görülen ÜÇ FARKLI format (hepsi aynı platformdan!):

    /api/disclosure/list/light      ->  "17.11.2026 15:05:53"   (DD.MM.YYYY)
    /api/notification/.../{idx}     ->  "2026.11.17 14:53:07"   (YYYY.MM.DD)
    /bildirim-sorgu-sonuc tablosu   ->  "05.11.2026" ve "Bugün 14:53"
    detaylı sorgu aralık etiketi    ->  "17-11-2025"            (DD-MM-YYYY)

Kural: her şey **aware UTC** datetime'a çevrilir (D10). TRT yalnızca gösterimde.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TRT = timezone(timedelta(hours=3), name="TRT")  # Europe/Istanbul sabit UTC+3 (2016'dan beri DST yok)

_YMD = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$")
_DMY_DT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})[ T](\d{2}):(\d{2})(?::(\d{2}))?$")
_DMY_D = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_DMY_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_RELATIVE = re.compile(r"^(bugün|dün)\s+(\d{2}):(\d{2})(?::(\d{2}))?$", re.IGNORECASE)


class UnparsableDate(ValueError):
    """Tanınmayan tarih formatı — sessizce None dönmek yerine patlar (bkz. triage felsefesi)."""


def _to_utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=TRT).astimezone(timezone.utc)


def parse_kap_datetime(raw: str, *, now: datetime | None = None) -> datetime:
    """KAP'tan gelen tarih metnini aware UTC datetime'a çevirir.

    Args:
        raw: Yukarıdaki formatlardan biri ("Bugün 14:53" dahil).
        now: Göreli ifadeler için referans (test edilebilirlik). Varsayılan: şimdi.

    Raises:
        UnparsableDate: format tanınmadıysa. Boş string yaygın olduğu için
            çağıran katman `None` kontrolünü kendi yapar.
    """
    if not raw or not raw.strip():
        raise UnparsableDate("boş tarih")
    s = raw.strip().replace("\u00a0", " ")

    if m := _YMD.match(s):
        y, mo, d, hh, mi, ss = m.groups()
        return _to_utc(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0))

    if m := _DMY_DT.match(s):
        d, mo, y, hh, mi, ss = m.groups()
        return _to_utc(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0))

    if m := _DMY_D.match(s) or _DMY_DASH.match(s):
        d, mo, y = m.groups()
        return _to_utc(int(y), int(mo), int(d))  # gün hassasiyeti -> 00:00 TRT

    if m := _RELATIVE.match(s):
        word, hh, mi, ss = m.groups()
        ref = (now or datetime.now(timezone.utc)).astimezone(TRT)
        day = ref.date() - (timedelta(days=1) if word.lower() == "dün" else timedelta(0))
        return _to_utc(day.year, day.month, day.day, int(hh), int(mi), int(ss or 0))

    raise UnparsableDate(f"tanınmayan tarih formatı: {raw!r}")


def try_parse_kap_datetime(raw: str, *, now: datetime | None = None) -> datetime | None:
    """Hata fırlatmayan sürüm; sadece 'bu alan yok/boş' durumları için."""
    try:
        return parse_kap_datetime(raw, now=now)
    except UnparsableDate:
        return None


def to_trt(dt: datetime) -> datetime:
    """Gösterim için TRT'ye çevir (DB'de her zaman UTC tutulur)."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime: UTC olduğunu varsaymıyoruz")
    return dt.astimezone(TRT)
