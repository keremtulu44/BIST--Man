"""Bir bildirim anında kullanılabilecek mali tablo sürümünü seç."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .parsing.dates import to_trt
from .sources.base import FundamentalRow


@dataclass(frozen=True, slots=True)
class PublishedRow:
    row: FundamentalRow
    published_at: datetime | None  # dönem sonu değil, bu SÜRÜMÜN yayını


def period_at(row: FundamentalRow, at: datetime) -> date | None:
    try:
        end = date.fromisoformat(row.period_end)
    except (TypeError, ValueError):
        return None
    return end if end <= to_trt(at).date() else None


def statement_at(rows: Sequence[PublishedRow], ticker: str, at: datetime) -> PublishedRow | None:
    candidates = []
    for version in rows:
        ts = version.published_at
        if version.row.ticker != ticker or ts is None or ts.utcoffset() is None or ts > at:
            continue
        end = period_at(version.row, ts)
        if end is not None:
            candidates.append((end, ts, version))
    # En son dönem, o dönemin haber anında bilinen en son revizyonu.
    return max(candidates, key=lambda v: v[:2])[2] if candidates else None


Snapshot = FundamentalRow | Sequence[PublishedRow]


def scoring_snapshot(snapshot: Snapshot | None, ticker: str, at: datetime) -> tuple[FundamentalRow | None, str | None]:
    if not isinstance(snapshot, FundamentalRow):
        version = statement_at(snapshot or (), ticker, at)
        row = version.row if version else None
        note = None
    else:
        row, note = snapshot, "tek snapshot: tablo yayın saati doğrulanmadı"
    if row and (row.ticker != ticker or row.currency != "TRY" or period_at(row, at) is None):
        return None, "tablo hisse, dönem veya para birimiyle eşleşmedi"
    return row, note
