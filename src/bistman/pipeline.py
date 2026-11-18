"""Uçtan uca hat: bildirim -> triyaj -> hisse -> oran -> skor -> bildirim.

Yan etkiler enjekte edilir (`Notifier`, `TickerResolver`, fundamentals sözlüğü),
bu yüzden tamamı **ağsız test edilebilir**. Replay de aynı arayüzü kullanır:
`FakeNotifier` + kayıtlı fundamentals -> yan etkisiz koşu (MIMARI.md §6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Awaitable, Callable, Literal, Mapping, Sequence

from .features import Features, build_features
from .fundamentals import Snapshot, scoring_snapshot
from .notify import Notifier, send_checked
from .parsing.evidence import ContractKind, EventAmount, event_amount, issuer_signature, sale_qualified
from .parsing.kap_table import body_text
from .parsing.text import symbols_in_title, tr_lower
from .scoring import Action, Decision, ScoringConfig, score
from .sources.base import DisclosureDetail, DisclosureStub
from .ticker_map import TickerResolver
from .triage import Route, is_index_agenda, looks_like_fund, route

Status = Literal[
    "scored",
    "filtered_routine",
    "filtered_mechanical",
    "filtered_fund",
    "fundamentals_refresh",
    "unresolved_ticker",
    "no_fundamentals",
    "agenda",
]

BodyProvider = Callable[[DisclosureStub], Awaitable[DisclosureDetail | str | None]]
"""Adayın gövdesini getiren opsiyonel çağrı. Detayın kimliği kontrol edilir.
Düz metin dönen eski sağlayıcıda index eşlemesi sağlayıcının sorumluluğudur.

Neden ayrı bir parametre: hat, gövde indirmeyi **sadece adaylar için** yapar
(günde onlarca, bin değil). Testlerde ağsız çalışsın diye enjekte edilir.
"""


# Dar gündem listesi: konu başlığı bilgi değerini taşır. Rakamdan zarar, yön
# veya yasak muhatabı çıkarmak yerine koşullu bir okuma ile yalnız özete girer.
_COMPANY_AGENDA = {
    "üretime ara verilmesi": "Duruşun süresi ve etkilenen hatlar, teslimat takvimine etkisini belirler.",
    "konkordato talebi": "Talebin sonucu borç ödeme takvimini etkileyebilir; başvuru, kabul kararı değildir.",
    "spk işlem yasağı nedeniyle pay duyurusu": "Yasağın muhatabı ve süresi önemlidir; bu, tüm payda işlemlerin durduğu anlamına gelmez.",
}


@dataclass(frozen=True, slots=True)
class ProcessedDisclosure:
    disclosure: DisclosureStub
    route: Route
    status: Status
    ticker: str | None = None
    features: Features | None = None
    decision: Decision | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    brief: str | None = None
    delivery: Literal["not_attempted", "acknowledged", "failed"] = "not_attempted"

    @property
    def qualitative(self) -> bool:
        return bool(self.brief) and (self.features is None or self.features.primary_ratio is None)

    @property
    def planned_action(self) -> Action | None:
        if self.qualitative:
            return "digest"
        return self.decision.action if self.decision else None

    @property
    def is_notified(self) -> bool:
        """Yalnız doğrudan gönderim yolunun onayı; `dispatch` ayrı rapor üretir."""
        return self.delivery == "acknowledged"


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
    if item.brief:
        lines.insert(1, item.brief)
    elif d.summary and d.summary.strip() not in {"", "-", "—"}:
        summary = d.summary.strip()
        lines.append(summary if len(summary) <= 300 else f"Uzun özet için bildirim #{d.index} metnine bakın.")
    if not item.brief:
        lines.append(" | ".join(item.decision.reasons[:3]))
    # `push_uncertain` etiketi mesajda GÖRÜNMELİ: yoksa kullanıcı skoru 2.50 olan
    # bir mesajı, gerekçesiz "küçük haber" sanır (impact 10 olsa bile).
    if item.decision.action == "push_uncertain":
        flags = ", ".join(item.features.flags) if item.features else ""
        detail = flags or "emniyet düşük"
        lines.append(f"⚠️ belirsiz: {detail} — tutar/oran doğrulanmalı")
    return "\n".join(lines)


async def process_batch(
    disclosures: Sequence[DisclosureStub],
    *,
    resolver: TickerResolver,
    fundamentals: Mapping[str, Snapshot],
    cfg: ScoringConfig,
    notifier: Notifier | None = None,
    fx: Mapping[str, Decimal] | None = None,
    body_provider: BodyProvider | None = None,
) -> list[ProcessedDisclosure]:
    """Bir grup bildirimi işler; dış kaynak hatası tek kayıtta raporlanır.

    `fundamentals` sürüm dizisi ise yalnız o anda bilinen tablo kullanılır.
    Tek snapshot eski demo/kalibrasyon yoludur; yayın anı doğrulanmış sayılmaz.

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

        if is_index_agenda(disclosure.title, disclosure.subject, disclosure.tickers):
            results.append(ProcessedDisclosure(
                disclosure, triage_route, "agenda",
                brief="Fiili dolaşım oranındaki değişiklik ilgili payların endeks ağırlıklarını etkileyebilir.",
            ))
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
                        notes=("tek hisse belirlenemedi; sembol listesini ve unvan→kod eşlemesini kontrol et",),
                    )
                )
            continue

        notes: list[str] = []
        fundamental, note = scoring_snapshot(fundamentals.get(ticker), ticker, disclosure.published_at)
        if note:
            notes.append(note)
        brief = _COMPANY_AGENDA.get(tr_lower(disclosure.subject.strip()))
        if brief:
            # İzlemeye değer ama parasal olay değil. Sayısal karar geçmiş
            # ölçümde kalır; bildirimde uydurma bir zarar/etki skoru gösterilmez.
            amount, signed = EventAmount(issue="no_amount"), None
        else:
            amount, signed = await _read_amount(disclosure, ticker, body_provider, fx, notes)
        amount_try, amount_flags = amount.as_try(fx)
        features = build_features(
            ticker=ticker,
            amount_try=amount_try,
            revenue_ttm=_dec(fundamental.revenue_ttm) if fundamental else None,
            net_income_ttm=_dec(fundamental.net_income_ttm) if fundamental else None,
            statement_type=(
                "bank" if fundamental and fundamental.statement_type == "bank" else "industrial"
            ),
            extra_flags=amount_flags + (fundamental.flags if fundamental else ("no_denominator",)),
        )
        decision = score(features, disclosure.subject, cfg)

        if fundamental is None:
            notes.append("finansal veri yok (TTM oranı hesaplanamadı)")

        if not brief:
            brief = _contract_brief(
                signed, amount, features, industrial=bool(fundamental and fundamental.statement_type == "industrial"),
            )

        item = ProcessedDisclosure(
            disclosure=disclosure,
            route=triage_route,
            status="scored" if fundamental else "no_fundamentals",
            ticker=ticker,
            features=features,
            decision=decision,
            notes=tuple(notes),
            brief=brief,
        )

        if notifier is not None and item.planned_action in {"push", "push_uncertain"}:
            ok = await send_checked(notifier, render_message(item), key=f"{disclosure.index}:{ticker}")
            item = replace(item, delivery="acknowledged" if ok else "failed")

        results.append(item)

    return results


async def _read_amount(
    disclosure: DisclosureStub, ticker: str, provider: BodyProvider | None,
    fx: Mapping[str, Decimal] | None, notes: list[str],
) -> tuple[EventAmount, ContractKind | None]:
    fields = [disclosure.subject, disclosure.summary or ""]
    subject = tr_lower(disclosure.subject)
    contract = "sözleşme" in subject or "ihale" in subject
    signed = issuer_signature(*fields)
    amount = event_amount(*fields, contract=contract or signed is not None)
    # Fiyatı belli bir sözleşmenin detayı *varsa* düzeltme/fesih içerebilir.
    # Buna karşılık sırf kur eksik diye gövdede çevrilebilir başka sayı arama.
    verify = (amount.money is not None and (contract or signed is not None)
              and (signed is not None or amount.money.to_try(fx) is not None))
    if provider is not None and (verify or amount.issue in {"no_amount", "unattributed_amount"}):
        try:
            detail = await provider(disclosure)
            if isinstance(detail, DisclosureDetail):
                if not detail.matches(disclosure, ticker):
                    notes.append("gövde kimliği, yayın zamanı veya yapısal hisse uyuşmadı")
                    return EventAmount(issue="detail_mismatch"), None
                body = body_text(detail.body_html)
            else:
                body = detail
        except Exception as exc:
            notes.append(f"gövde alınamadı ({type(exc).__name__})")
            body = None
        if body and body.strip():
            fields.append(body)
            signed = issuer_signature(*fields)
            amount = event_amount(*fields, contract=contract or signed is not None)
        elif verify:
            notes.append("sözleşme bedeli detaydan kontrol edilemedi")
            return EventAmount(issue="detail_unavailable"), signed
    if (contract or signed) and sale_qualified(*fields):
        return EventAmount(issue="qualified_contract"), None
    if "ihale" in subject and not signed:
        return EventAmount(issue="unconfirmed_tender"), None
    return amount, signed


def _contract_brief(
    signed: ContractKind | None, amount: EventAmount, features: Features, industrial: bool,
) -> str | None:
    if amount.issue == "qualified_contract":
        return "Açıklamada iptal, itiraz veya koşul ifadesi var; bedel kesinleşmiş yeni satış olarak oranlanmadı."
    if not signed:
        return None
    if amount.issue == "detail_unavailable":
        return "Şirket imza bildirdi; detay alınamadığı için bedel teyit edilmedi ve oran verilmedi."

    raw = f" ({amount.money.raw})" if amount.money else ""
    if features.primary_ratio is None:
        event = (f"İmzalanan satış{raw} teslimatlarla ciroyu destekleyebilir."
                 if signed == "sale" else f"Sözleşme imzalandı{raw}.")
        if amount.money is None:
            limit = "Şirkete ait kesin bedel belirlenemedi."
        elif amount.money.currency == "UNKNOWN":
            limit = "Para birimi belirsiz; TL karşılığı hesaplanmadı."
        elif "fx_fallback" in features.flags:
            limit = "Kullanılabilir kur yok; TL karşılığı ve ciroya oranı hesaplanmadı."
        else:
            limit = "Karşılaştırılabilir ciro yok; oran hesaplanmadı."
        return f"{event} {limit}"
    if signed == "sale" and industrial and features.ratio_revenue is not None:
        pct = f"{features.ratio_revenue:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return (f"Satış bedelinin{raw} son 12 aylık ciroya oranı %{pct}. "
                "Teslimatlar satışları destekleyebilir; bedel kâr değildir.")
    return None


def _resolve_ticker(disclosure: DisclosureStub, resolver: TickerResolver) -> str | None:
    """Yapısal ticker varsa onu kullan (detay ucu), yoksa unvandan çöz.

    Canlı akış (`/api/disclosure/list/light`) ticker vermiyor; platform
    bildirimlerinin başlığı ise şirket unvanı değil "BORSA İSTANBUL ... / MTRKS"
    biçiminde. Gerçek 89 satırlık geçmişte bu satırların **%33'ü** yalnız
    unvanla çözülemiyordu; başlıktaki tek sembol o boşluğun çoğunu kapatıyor.
    Çok sembollü başlık (piyasa geneli duyuru) hâlâ belirsiz: tahmin yok.
    """
    # Detay ve üye geçmişi yapısal kod taşır. Çoklu koddan ilkini seçmek,
    # aynı tutarı ilgisiz bir şirketin bilançosuna bölebilir.
    codes = tuple(dict.fromkeys(code for code in disclosure.tickers if code))
    if codes:
        return codes[0] if len(codes) == 1 else None
    symbols = tuple(dict.fromkeys(symbols_in_title(disclosure.title)))
    if len(symbols) > 1:
        return None
    return resolver.resolve(disclosure.title).ticker or (symbols[0] if symbols else None)


def _dec(value: float | None) -> Decimal | None:
    number = None if value is None else Decimal(str(value))
    return number if number is not None and number.is_finite() else None


def summarize(results: Sequence[ProcessedDisclosure]) -> dict[str, int]:
    """Faz raporu için sayaçlar (log/metric olarak basılır)."""
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    counts["push_candidates"] = sum(item.planned_action in {"push", "push_uncertain"} for item in results)
    counts["push_attempted"] = sum(item.delivery != "not_attempted" for item in results)
    counts["push_failed"] = sum(item.delivery == "failed" for item in results)
    counts["notified"] = sum(item.is_notified for item in results)
    return counts
