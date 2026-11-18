"""Uçtan uca hat: bildirim -> triyaj -> hisse -> oran -> skor -> bildirim.

Yan etkiler enjekte edilir (`Notifier`, `TickerResolver`, fundamentals sözlüğü),
bu yüzden tamamı **ağsız test edilebilir**. Replay de aynı arayüzü kullanır:
`FakeNotifier` + kayıtlı fundamentals -> yan etkisiz koşu (MIMARI.md §6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Literal, Mapping, Protocol, Sequence

from .features import Features, amount_try_from_text, build_features
from .scoring import Decision, ScoringConfig, score
from .sources.base import DisclosureStub, FundamentalRow
from .parsing.text import symbols_in_title
from .ticker_map import TickerResolver
from .triage import Route, looks_like_fund, route

Status = Literal[
    "scored",
    "filtered_routine",
    "filtered_mechanical",
    "filtered_fund",
    "fundamentals_refresh",
    "unresolved_ticker",
    "no_fundamentals",
]

BodyProvider = Callable[[DisclosureStub], Awaitable[str | None]]
"""Özet metninde tutar yoksa gövdeyi getiren opsiyonel çağrı (KAP detay ucu).

Neden ayrı bir parametre: hat, gövde indirmeyi **sadece adaylar için** yapar
(günde onlarca, bin değil). Testlerde ağsız çalışsın diye enjekte edilir.
"""


class Notifier(Protocol):
    async def send(self, message: str, *, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProcessedDisclosure:
    disclosure: DisclosureStub
    route: Route
    status: Status
    ticker: str | None = None
    features: Features | None = None
    decision: Decision | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_notified(self) -> bool:
        return self.decision is not None and self.decision.should_notify


def render_message(item: ProcessedDisclosure) -> str:
    """Kullanıcıya gidecek metin — Llm'siz, tamamen kural gerekçesi."""
    d = item.disclosure
    assert item.decision is not None
    head = f"{item.ticker or '?'} · {d.subject}"
    lines = [
        head,
        f"impact {item.decision.impact:.1f} · emniyet {item.decision.confidence:.2f}"
        f" · skor {item.decision.push_score:.2f}",
    ]
    if d.summary:
        lines.append(d.summary.strip()[:300])
    lines.append(" | ".join(item.decision.reasons[:3]))
    return "\n".join(lines)


async def process_batch(
    disclosures: Sequence[DisclosureStub],
    *,
    resolver: TickerResolver,
    fundamentals: Mapping[str, FundamentalRow],
    cfg: ScoringConfig,
    notifier: Notifier | None = None,
    fx: Mapping[str, Decimal] | None = None,
    body_provider: BodyProvider | None = None,
) -> list[ProcessedDisclosure]:
    """Bir grup bildirimi uçtan uca işler. Hiçbir adım exception fırlatmaz;
    her sonuç `status` + `notes` ile raporlanır.

    `notifier` verilirse push'lar **burada, geliş sırasıyla** gider. Üretimde
    `notifier` verilmez: sıralama ve günlük bütçe `digest.dispatch` içinde
    uygulanır (önem sırası + tek özet). Buradaki yol replay/test içindir.
    """
    results: list[ProcessedDisclosure] = []

    for disclosure in disclosures:
        triage_route = route(disclosure.disclosure_class, disclosure.subject, disclosure.summary)

        if triage_route is not Route.PIPELINE:
            status: Status = {
                Route.ROUTINE: "filtered_routine",
                Route.MECHANICAL: "filtered_mechanical",
                Route.FUNDAMENTALS_REFRESH: "fundamentals_refresh",
            }[triage_route]
            results.append(
                ProcessedDisclosure(disclosure=disclosure, route=triage_route, status=status)
            )
            continue

        ticker = _resolve_ticker(disclosure, resolver)
        if ticker is None:
            if looks_like_fund(disclosure.title):
                results.append(
                    ProcessedDisclosure(
                        disclosure=disclosure, route=triage_route, status="filtered_fund"
                    )
                )
            else:
                results.append(
                    ProcessedDisclosure(
                        disclosure=disclosure,
                        route=triage_route,
                        status="unresolved_ticker",
                        notes=("unvan eşleşmedi; unvan→kod tablosunu güncelle",),
                    )
                )
            continue

        fundamental = fundamentals.get(ticker)
        text = " ".join(x for x in (disclosure.subject, disclosure.summary) if x)
        amount_try, amount_flags = amount_try_from_text(text, fx)

        # Özette tutar yoksa gövdeyi dene — ama yalnız adaylar için (maliyet kontrolü)
        if amount_try is None and body_provider is not None:
            body = await body_provider(disclosure)
            if body:
                amount_try, amount_flags = amount_try_from_text(f"{text} {body}", fx)

        features = build_features(
            ticker=ticker,
            amount_try=amount_try,
            revenue_ttm=_dec(fundamental.revenue_ttm) if fundamental else None,
            net_income_ttm=_dec(fundamental.net_income_ttm) if fundamental else None,
            statement_type=(
                "bank" if fundamental and fundamental.statement_type == "bank" else "industrial"
            ),
            extra_flags=amount_flags + (() if fundamental else ("no_denominator",)),
        )
        decision = score(features, disclosure.subject, cfg)

        notes: list[str] = []
        if fundamental is None:
            notes.append("finansal veri yok (TTM oranı hesaplanamadı)")

        item = ProcessedDisclosure(
            disclosure=disclosure,
            route=triage_route,
            status="scored" if fundamental else "no_fundamentals",
            ticker=ticker,
            features=features,
            decision=decision,
            notes=tuple(notes),
        )

        if notifier is not None and decision.should_notify:
            await notifier.send(render_message(item), key=f"{disclosure.index}:{ticker}")

        results.append(item)

    return results


def _resolve_ticker(disclosure: DisclosureStub, resolver: TickerResolver) -> str | None:
    """Yapısal ticker varsa onu kullan (detay ucu), yoksa unvandan çöz.

    Canlı akış (`/api/disclosure/list/light`) ticker vermiyor; platform
    bildirimlerinin başlığı ise şirket unvanı değil "BORSA İSTANBUL ... / MTRKS"
    biçiminde. Gerçek 89 satırlık geçmişte bu satırların **%33'ü** yalnız
    unvanla çözülemiyordu; başlıktaki tek sembol o boşluğun çoğunu kapatıyor.
    Çok sembollü başlık (piyasa geneli duyuru) hâlâ belirsiz: tahmin yok.
    """
    if disclosure.tickers and disclosure.tickers[0]:
        return disclosure.tickers[0]
    resolution = resolver.resolve(disclosure.title)
    if resolution.ticker:
        return resolution.ticker
    symbols = symbols_in_title(disclosure.title)
    if len(symbols) == 1:
        return symbols[0]
    return None


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def summarize(results: Sequence[ProcessedDisclosure]) -> dict[str, int]:
    """Faz raporu için sayaçlar (log/metric olarak basılır)."""
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    counts["notified"] = sum(1 for item in results if item.is_notified)
    return counts
