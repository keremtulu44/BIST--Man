"""Backfill → kalibrasyon hattı: KAP geçmişi + fiyat = etiketli olay listesi.

Akış:

    HistoryRow (üye geçmişi, 1 istek = 1 yıl)
        │  attribute_ticker: 'Kod' -> üye kodu -> ilgili hisse
        ▼
    DisclosureStub  ──► process_batch (triaj + oran + skor)  = push_score
        │
        ▼
    compute_event (fiyat + endeks)                           = z1 / car1
        │
        ▼
    ScoredEvent  ──► calibration.sweep                       = eşik × bütçe tablosu

Atıf kuralları (gerçek tabloda doğrulandı):

1. **Üye kodu kazanır** — `Kod`, `İlgili hisseler` ya da başlıktaki `/` sonrası
   sembol listesinde üye geçiyorsa satır o üyeye aittir. Gerekçe: MTRKS sayfasında
   "İŞ PORTFÖY ... Pay Alım Satım Bildirimi / IDH, IEV, ..., MTRKS, ..." satırının
   `Kod` sütunu **ISP** yazıyor; ihraççı fon yöneticisi, konu ise MTRKS. Eski kural
   bu satırları ISP'ye yazıp MTRKS'in geçmişinden siliyordu.
2. `Kod` doluysa o (ihraççı).
3. `Kod` boşsa (DKB satırları) **üye kodu**.
4. O da yoksa ilk ilgili hisse; `(+N)` ile kesikse `notes`'a yazılır.

Fon akışı istisnası: başlığında 3+ sembol sıralayan fon/portföy bildirimleri
(tek duyuru = onlarca hisse) hisse bazlı olay sayılmaz; `select_rows` bunları
`fund_flow` gerekçesiyle dışlar ve sayar.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .analysis.calibration import ScoredEvent
from .analysis.event_study import Series, compute_event
from .parsing.kap_table import HistoryRow
from .pipeline import ProcessedDisclosure, process_batch
from .parsing.text import symbols_in_title, tr_lower
from .scoring import ScoringConfig
from .sources.base import DisclosureStub, FundamentalRow
from .ticker_map import TickerResolver

TRT = timezone(timedelta(hours=3))

__all__ = [
    "FUND_FLOW_MIN_SYMBOLS",
    "MULTI_SYMBOL_MIN",
    "attribute_ticker",
    "is_bulk_platform",
    "is_fund_flow",
    "row_symbols",
    "select_rows",
    "symbols_in_title",
    "build_scored_events",
    "scored_from_results",
    "stub_from_row",
]


def row_symbols(row: HistoryRow) -> set[str]:
    """Satırın dokunduğu tüm semboller: Kod ∪ İlgili ∪ başlık listesi."""
    return set(row.tickers) | set(row.related) | set(symbols_in_title(row.company_title))


def attribute_ticker(row: HistoryRow, member_ticker: str | None) -> str | None:
    """Bir geçmiş satırını hisseye bağlar. Belirsizse `None` (tahmin yok)."""
    if member_ticker and member_ticker in row_symbols(row):
        return member_ticker
    if row.tickers:
        return row.tickers[0]
    if member_ticker:
        return member_ticker
    return row.related[0] if row.related else None


MULTI_SYMBOL_MIN = 3
"""Bu kadar sembol tek duyuruda: satır artık "tek hisse olayı" değil."""

_FUND_MANAGER_MARKERS: tuple[str, ...] = (
    "portföy",
    "portfoy",
    "yatırım fonu",
    "yatirim fonu",
    "varlık yönetimi",
    "varlik yonetimi",
)


def is_fund_flow(row: HistoryRow) -> bool:
    """Fon/portföy yöneticisinin çok hisseli bildirimi: portföy hareketi.

    "İŞ PORTFÖY ... Pay Alım Satım Bildirimi / IDH, IEV, ..., MTRKS, ..." gibi.
    Hangi hisseye ne kadar alındığı belli değil, hisse bazlı bilgi yok; skorlarsak
    kalibrasyon örneklemini gürültüyle doldurur. **Dışlanır** (sayılarak).
    """
    if len(symbols_in_title(row.company_title)) < MULTI_SYMBOL_MIN:
        return False
    title = tr_lower(row.company_title)
    return any(marker in title for marker in _FUND_MANAGER_MARKERS)


def is_bulk_platform(row: HistoryRow) -> bool:
    """MKK/KAP çok hisseli duyurusu ('İşlem Yasağı', 'Tipe Dönüşüm', 77 sembol).

    Portföy hareketi değil: SPK işlem yasağı gibi **gerçek** bir bilgi şoku, ama
    tek duyuru onlarca hisseyi kapsıyor. Dışlanmaz — hisse bazlı CAR ölçülebilir;
    payı raporda görünür ki tablo buna göre okunsun.
    """
    return (
        len(symbols_in_title(row.company_title)) >= MULTI_SYMBOL_MIN
        and not is_fund_flow(row)
    )


def select_rows(
    rows: Sequence[HistoryRow], member_ticker: str | None
) -> tuple[list[HistoryRow], dict[str, int]]:
    """Üye sayfasındaki satırlardan kalibrasyona girecekleri seçer.

    Dönen ikinci değer gerekçe sayacıdır (`fund_flow`, `unattributed`, `foreign`).
    Sessizce elemek yok: her eleme sayılır ve raporda görünür.
    """
    kept: list[HistoryRow] = []
    skipped: dict[str, int] = {}
    for row in rows:
        if is_fund_flow(row):
            skipped["fund_flow"] = skipped.get("fund_flow", 0) + 1
            continue
        if is_bulk_platform(row):
            skipped["bulk_platform"] = skipped.get("bulk_platform", 0) + 1  # sayılır, atılmaz
        ticker = attribute_ticker(row, member_ticker)
        if ticker is None:
            skipped["unattributed"] = skipped.get("unattributed", 0) + 1
            continue
        if member_ticker and ticker != member_ticker:
            # Başka ihraççının satırı: kendi sayfasında sayılır, burada çiftlenmesin.
            skipped["foreign"] = skipped.get("foreign", 0) + 1
            continue
        kept.append(row)
    return kept, skipped


def _as_utc(moment: datetime) -> datetime:
    """Naif zaman damgası **TRT** kabul edilir (KAP tablosu yerel saat yazar).

    Event study t0'ı seans takvimine çevirirken saat dilimi şart: 18:00 TRT
    yayını ertesi seansa düşmeli, 10:00 TRT yayını aynı güne.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=TRT).astimezone(timezone.utc)
    return moment.astimezone(timezone.utc)


def stub_from_row(row: HistoryRow, ticker: str) -> DisclosureStub:
    """Geçmiş satırını hattın beklediği `DisclosureStub`'a çevirir (saat UTC'ye)."""
    return DisclosureStub(
        index=row.index,
        published_at=_as_utc(row.published_at),
        title=row.company_title,
        subject=row.subject,
        summary=row.summary or None,
        disclosure_class=row.disclosure_class,
        tickers=(ticker,),
    )


def scored_from_results(
    results: Sequence[ProcessedDisclosure],
) -> list[ScoredEvent]:
    """Skorlanmış bildirimleri `ScoredEvent`'e çevirir (event study henüz yok)."""
    events: list[ScoredEvent] = []
    for item in results:
        if item.ticker is None or item.decision is None:
            continue
        if item.status not in {"scored", "no_fundamentals"}:
            continue
        events.append(
            ScoredEvent(
                ticker=item.ticker,
                t0=item.disclosure.published_at.date(),  # event study gerçek t0'yı yazar
                score=float(item.decision.push_score),
            )
        )
    return events


def attach_event_study(
    results: Sequence[ProcessedDisclosure],
    stocks: Mapping[str, Series],
    market: Series,
) -> tuple[list[ScoredEvent], list[str]]:
    """Skor + fiyat tepkisini birleştirir. Dönüş: (olaylar, neden sayımı).

    Kullanılamayan olay **atılmaz**, nedeni sayılır: kalibrasyondan çıkan olay
    sayısı raporun en kritik sayısıdır (az veri = zayıf eşik).
    """
    events: list[ScoredEvent] = []
    reasons: dict[str, int] = {}

    for item in results:
        if item.ticker is None or item.decision is None:
            continue
        if item.status not in {"scored", "no_fundamentals"}:
            continue
        stock = stocks.get(item.ticker)
        if stock is None:
            reasons["no_price_series"] = reasons.get("no_price_series", 0) + 1
            continue

        result = compute_event(stock, market, item.disclosure.published_at)
        if not result.usable:
            key = result.reason or "unusable"
            reasons[key] = reasons.get(key, 0) + 1
            continue

        events.append(
            ScoredEvent(
                ticker=item.ticker,
                t0=result.t0 or item.disclosure.published_at.date(),
                score=float(item.decision.push_score),
                z1=result.z.get(1),
                car1=result.car.get(1),
            )
        )
    return events, sorted(reasons.items())


async def build_scored_events(
    rows_by_member: Mapping[str, Sequence[HistoryRow]],
    *,
    cfg: ScoringConfig,
    fundamentals: Mapping[str, FundamentalRow],
    resolver: TickerResolver,
    notifier: Any | None = None,
    body_provider: Any | None = None,
) -> list[ProcessedDisclosure]:
    """Her üyenin geçmişini hattan geçirir (ağ yok; satırlar hazır).

    `body_provider` verilirse özette tutar bulunamayan adaylar için gövde
    çekilir — gerçek skorların tek kaynağı bu (geçmiş tablosunda tutar yok).
    """
    results: list[ProcessedDisclosure] = []
    for member_ticker, rows in rows_by_member.items():
        stubs: list[DisclosureStub] = []
        for row in rows:
            ticker = attribute_ticker(row, member_ticker)
            if ticker is None:
                continue
            stubs.append(stub_from_row(row, ticker))
        if not stubs:
            continue
        results.extend(
            await process_batch(
                stubs,
                resolver=resolver,
                fundamentals=fundamentals,
                cfg=cfg,
                notifier=notifier,
                body_provider=body_provider,
            )
        )
    return results


def build_scored_events_sync(
    rows_by_member: Mapping[str, Sequence[HistoryRow]],
    *,
    cfg: ScoringConfig,
    fundamentals: Mapping[str, FundamentalRow],
    resolver: TickerResolver,
    notifier: Any | None = None,
    body_provider: Any | None = None,
) -> list[ProcessedDisclosure]:
    """`build_scored_events`'in senkron sarmalayıcısı (script'ler için)."""
    return asyncio.run(
        build_scored_events(
            rows_by_member,
            cfg=cfg,
            fundamentals=fundamentals,
            resolver=resolver,
            notifier=notifier,
            body_provider=body_provider,
        )
    )


@dataclass(frozen=True, slots=True)
class BackfillCoverage:
    """Backfill'in ne kadarını gerçekten aldığımızın özeti (dürüstlük sayfası)."""

    members: int
    rows: int
    attributed: int
    multi_symbol: int
    truncated_related: int

    @property
    def attribution_rate(self) -> float:
        return self.attributed / self.rows if self.rows else 0.0


def coverage(rows_by_member: Mapping[str, Sequence[HistoryRow]]) -> BackfillCoverage:
    rows = 0
    attributed = 0
    multi = 0
    truncated = 0
    for member_ticker, member_rows in rows_by_member.items():
        for row in member_rows:
            rows += 1
            if attribute_ticker(row, member_ticker):
                attributed += 1
            if len(row.related) > 1:
                multi += 1
            if row.related_truncated:
                truncated += 1
    return BackfillCoverage(
        members=len(rows_by_member),
        rows=rows,
        attributed=attributed,
        multi_symbol=multi,
        truncated_related=truncated,
    )


def events_to_points(events: Iterable[ScoredEvent]) -> list[tuple[datetime, float, float | None]]:
    """Raporlama için düz liste: (t0, skor, z1)."""
    return [(datetime.combine(e.t0, datetime.min.time()), e.score, e.z1) for e in events]
