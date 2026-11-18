"""FAZ 4 — GERÇEK KALİBRASYON: KAP geçmişi + fiyat tepkisi -> eşik tablosu.

Ne yapıyor (sırayla):

  1. KAP `/tr/bist-sirketler` -> {kod: mkkMemberOid}
  2. Her hisse için üye geçmişi (1 istek = 1 yıl) -> HistoryRow[]
  3. Satır seçimi: fon/portföy akışı dışlanır, MKK toplu duyuruları sayılır
  4. Finansal veri: İş Yatırım MaliTablo (sanayi XI_29 / banka UFRS)
  5. Tutar: özette yoksa bildirim **gövdesi** (1 istek/aday, diskte önbellek)
  6. Hattan geçir: triaj -> oran -> skor (push_score)
  7. Fiyat: hisse (Yahoo) + XU100 (İş Yatırım) -> CAR(1), z(1)
  8. Eşik × bütçe ızgarası -> isabet / kaçan / push-per-gün
  9. Gerçek koşu: docs/kalibrasyon-sonuc.md + config/scoring.measured.toml. Dry-run yalnız ekrana yazar.

Kullanım:

    python scripts\\kalibrasyon_gercek.py --dry-run            # AĞ YOK: fixture + sentetik fiyat
    python scripts\\kalibrasyon_gercek.py --limit 5            # ilk 5 hisse
    python scripts\\kalibrasyon_gercek.py --no-bodies          # gövde indirme (hızlı, skorlar düşük)
    python scripts\\kalibrasyon_gercek.py                      # tam koşu

Hisse başına geçmiş ve fiyat dışında her mali tablo şablonu için 3 istek yapılır.
Gövde yalnız adaylar için indirilir. Gövdeler ve fiyatlar önbelleklenir; istek
hesabı ve gerçek koşunun sınırları için bkz. docs/FAZ4-KALIBRASYON.md.

Gerçek veride **tutarlar özet metninde yok**: geçmiş tablosu yalnız konu/özet
verir. Tutar gövdede. `--no-bodies` ile koşarsan skorlar ~0 çıkar; bu bir hata
değil, ölçümün neye dayandığının kanıtıdır.

`config/scoring.toml` **elle değiştirilmez**: ölçüm `scoring.measured.toml`'a
yazılır, karşılaştırılıp bilinçli olarak kopyalanır.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.analysis.calibration import ScoredEvent, sweep  # noqa: E402
from bistman.analysis.event_study import Series  # noqa: E402
from bistman.backfill import (  # noqa: E402
    attach_event_study,
    build_scored_events,
    coverage,
    select_rows,
)
from bistman.notify import FakeNotifier  # noqa: E402
from bistman.parsing.kap_table import HistoryRow, parse_history  # noqa: E402
from bistman.scoring import ScoringConfig  # noqa: E402
from bistman.sources.base import FundamentalRow  # noqa: E402
from bistman.sources.isyatirim_client import IsYatirimClient, IsYatirimError  # noqa: E402
from bistman.sources.prices import PriceSource, fetch_series  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "kap"
DATA = ROOT / "data"
UNIVERSE_FALLBACK = ROOT / "config" / "universe_fallback.txt"
REPORT = ROOT / "docs" / "kalibrasyon-sonuc.md"
MEASURED = ROOT / "config" / "scoring.measured.toml"

THRESHOLDS = (3.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5)
BUDGETS: tuple[int | None, ...] = (3, None)
MATERIAL_Z = 2.0


@dataclass
class MemberData:
    ticker: str
    oid: str
    rows: list[HistoryRow] = field(default_factory=list)
    fundamentals: FundamentalRow | None = None
    error: str | None = None


# ------------------------------------------------------------------ evren


def read_universe() -> list[str]:
    if not UNIVERSE_FALLBACK.exists():
        raise SystemExit(f"evren dosyası yok: {UNIVERSE_FALLBACK}")
    tickers = [
        line.strip().upper()
        for line in UNIVERSE_FALLBACK.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not tickers:
        raise SystemExit("evren dosyası boş")
    return tickers


async def load_fundamentals(ticker: str) -> FundamentalRow:
    """Sanayi şablonuyla dene; gelir yoksa banka şablonuna düş."""
    client = IsYatirimClient()
    row = await client.fundamentals(ticker, statement_type="industrial")
    if row.revenue_ttm is not None:
        return row
    bank = await client.fundamentals(ticker, statement_type="bank")
    if bank.revenue_ttm is not None:
        return bank
    return row


# ------------------------------------------------------------------ toplama


async def collect(tickers: list[str], *, limit: int | None) -> tuple[list[MemberData], list[str]]:
    from bistman.sources.kap import KapClient
    from bistman.sources.kap_companies import fetch_company_map, missing_tickers

    chosen = tickers[:limit] if limit else tickers
    async with KapClient() as kap:
        company_map = await fetch_company_map(kap)
        absent = missing_tickers(company_map, chosen)
        print(f"KAP şirket listesi: {len(company_map)} kod · bulunamayan: {len(absent)}")

        members: list[MemberData] = []
        for i, ticker in enumerate(chosen, 1):
            oid = company_map.get(ticker)
            if not oid:
                members.append(MemberData(ticker, "", error="OID yok"))
                continue
            try:
                rows = await kap.fetch_history(oid)
            except Exception as exc:  # tek hisse tüm koşuyu durdurmasın
                members.append(MemberData(ticker, oid, error=f"geçmiş: {exc}"))
                continue
            try:
                fundamentals = await load_fundamentals(ticker)
            except (IsYatirimError, OSError) as exc:
                members.append(MemberData(ticker, oid, rows, error=f"mali tablo: {exc}"))
                continue
            members.append(MemberData(ticker, oid, rows, fundamentals))
            print(f"[{i}/{len(chosen)}] {ticker}: {len(rows)} bildirim")
    return members, absent


def make_body_provider(kap, *, max_bodies: int, cache_dir: Path = DATA / "bodies"):
    """Gövde indirici + disk önbelleği. Tutar yalnız gövdede olduğu için şart."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    state = {"fetched": 0, "cached": 0, "failed": 0}

    async def provider(stub) -> str:
        path = cache_dir / f"{stub.index}.txt"
        if path.exists():
            state["cached"] += 1
            return path.read_text(encoding="utf-8")
        if state["fetched"] >= max_bodies:
            return ""
        try:
            body, _detail = await kap.fetch_body(stub.index)
        except Exception:
            state["failed"] += 1
            return ""
        state["fetched"] += 1
        path.write_text(body or "", encoding="utf-8")
        if state["fetched"] % 25 == 0:
            print(f"  gövde: {state['fetched']} indirildi, {state['cached']} önbellek")
        return body or ""

    provider.state = state  # type: ignore[attr-defined]
    return provider


def load_prices(members: list[MemberData], *, days: int = 500) -> tuple[dict[str, Series], Series | None]:
    stocks: dict[str, Series] = {}
    for member in members:
        if not member.rows:
            continue
        try:
            stocks[member.ticker] = fetch_series(
                member.ticker, PriceSource("stock"), days=days, cache_dir=DATA / "prices"
            )
        except Exception as exc:
            print(f"  ! {member.ticker} fiyat alınamadı: {exc}")
    try:
        market = fetch_series("XU100", PriceSource("index"), days=days, cache_dir=DATA / "prices")
    except Exception as exc:
        print(f"  ! XU100 alınamadı: {exc}")
        market = None
    return stocks, market


def synthetic_prices(members: list[MemberData]) -> tuple[dict[str, Series], Series]:
    """--dry-run: gerçek tarih aralığında deterministik rastgele yürüyüş."""
    rng = random.Random(42)
    days = sorted({row.published_at.date() for m in members for row in m.rows})
    if not days:
        days = [date.today()]
    start, end = days[0] - timedelta(days=140), max(days) + timedelta(days=20)
    calendar: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # kaba seans takvimi (tatiller yok)
            calendar.append(cursor)
        cursor += timedelta(days=1)

    def walk(step: float, base: float) -> tuple[tuple[date, float], ...]:
        value = base
        bars = []
        for day in calendar:
            value *= 1 + rng.gauss(0, step)
            bars.append((day, round(value, 4)))
        return tuple(bars)

    stocks = {m.ticker: Series(m.ticker, walk(0.02, 10.0)) for m in members if m.rows}
    return stocks, Series("XU100", walk(0.008, 9000.0))


# ------------------------------------------------------------------ ölçüm


def score_stats(events: list[ScoredEvent], cfg: ScoringConfig) -> dict[str, float]:
    scores = sorted(e.score for e in events)
    if not scores:
        return {"n": 0}
    return {
        "n": len(scores),
        "min": scores[0],
        "median": statistics.median(scores),
        "max": scores[-1],
        "ge_digest": sum(1 for s in scores if s >= cfg.digest_threshold),
        "ge_push": sum(1 for s in scores if s >= cfg.push_threshold),
    }


def render_report(
    *,
    members: list[MemberData],
    events: list[ScoredEvent],
    reasons: list[tuple[str, int]],
    skips: dict[str, int],
    reports,
    cov,
    absent: list[str],
    stats: dict[str, float],
    bodies: dict[str, int] | None,
    dry_run: bool,
    cfg: ScoringConfig,
) -> str:
    material = [e for e in events if e.z1 is not None and abs(e.z1) >= MATERIAL_Z]
    lines = [
        "# Kalibrasyon Sonucu",
        "",
        f"Üretim: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"· {'DRY-RUN (sentetik fiyat)' if dry_run else 'GERÇEK VERİ'}",
        "",
        "> Hisse başına tek bilanço kullanılır. Eski haberler, o gün henüz yayımlanmamış",
        "> bir bilançoyla oranlanmış olabilir. Bu tablo geçmiş performans kanıtı değildir.",
        "",
        "## Kapsam",
        "",
        f"- Hisse: **{cov.members}** · KAP'ta bulunamayan: {len(absent)}",
        f"- Bildirim satırı: **{cov.rows}** (atfedilen: {cov.attributed}, "
        f"%{cov.attribution_rate * 100:.0f})",
        f"- Çok sembollü duyuru: {cov.multi_symbol} · kesik ilgili listesi: {cov.truncated_related}",
        f"- Fiyat tepkisi hesaplanan olay: **{len(events)}** · |z|≥{MATERIAL_Z}: **{len(material)}**",
        "",
    ]
    if skips:
        lines += ["Satır seçimi (sessiz eleme yok):", ""]
        lines += [f"- `{name}`: {count}" for name, count in sorted(skips.items())]
        lines.append("")
    if absent:
        lines += ["KAP şirket listesinde bulunamayan kodlar:", "", ", ".join(absent), ""]

    lines += ["## Skor dağılımı", ""]
    if stats.get("n", 0):
        lines += [
            f"- olay: {stats['n']:.0f} · en düşük {stats['min']:.2f} · "
            f"medyan {stats['median']:.2f} · en yüksek {stats['max']:.2f}",
            f"- digest eşiğini ({cfg.digest_threshold}) geçen: {stats['ge_digest']:.0f} · "
            f"push eşiğini ({cfg.push_threshold}) geçen: {stats['ge_push']:.0f}",
            "",
        ]
        if stats["ge_push"] == 0:
            lines += [
                "> ⚠️ **Hiçbir olay push eşiğini geçmedi.** Eşik tablosu bu örnekte",
                "> ayırt edici değil — nedeni genelde tutarın bulunamamasıdır (gövde",
                "> indirilmediyse skor yalnız konu önselinden gelir). `--no-bodies` ile",
                "> koştuysan tabloyu öyle okuma.",
                "",
            ]
    else:
        lines += ["Olay yok: fiyat serisi veya finansal veri eksik.", ""]

    if bodies is not None:
        lines += [
            "## Gövde indirme",
            "",
            f"- indirilen: {bodies.get('fetched', 0)} · önbellekten: {bodies.get('cached', 0)} · "
            f"başarısız: {bodies.get('failed', 0)}",
            "",
        ]

    if reasons:
        lines += ["Hesaplanamayan olay nedenleri:", ""]
        lines += [f"- `{name}`: {count}" for name, count in reasons]
        lines.append("")

    if len(events) < 30:
        lines += [
            f"> ⚠️ **Örneklem küçük ({len(events)} olay).** Tablo karar için değil,",
            "> mekanizma içindir. Eşik seçimi en az birkaç yüz olay ister.",
            "",
        ]

    lines += [
        "## Eşik × bütçe ızgarası",
        "",
        "```",
        *[report.as_row() for report in reports],
        "```",
        "",
        "Okunuşu: `isabet` = gönderilenler içinde |z|≥2 olanların oranı; `kaçan` =",
        "maddi olduğu halde gönderilmeyenler. Yüksek eşik isabeti artırır, kaçanı da",
        "artırır. Karar bu iki sayının hangisi daha pahalı olduğuna bağlı.",
        "",
        "## Mevcut ayar",
        "",
        f"- push eşiği: **{cfg.push_threshold}** · digest: {cfg.digest_threshold}",
        f"- günlük push bütçesi: {cfg.max_push_per_ticker_per_day}",
        "",
        "## Sıradaki adım",
        "",
        "1. Örneklemi büyüt (evrenin tamamı, 2 yıl).",
        "2. `kaçan` maddi olayları tek tek oku: kural mı kaçırdı, eşik mi yüksek?",
        "3. Eşikleri ancak gerçek veride, haber anında yayımlanmış bilançolarla ve ayrı bir dönemde doğruladıktan sonra taşı.",
        "",
    ]
    return "\n".join(lines)


def write_measured(events: list[ScoredEvent], reports, cfg: ScoringConfig) -> None:
    scored = [r for r in reports if r.pushed]
    best = max(scored, key=lambda r: (r.precision, -r.missed_material)) if scored else None
    lines = [
        "# ÖLÇÜLMÜŞ DEĞERLER — otomatik üretildi, elle düzenlenmez.",
        "# `config/scoring.toml`'a taşımadan önce docs/kalibrasyon-sonuc.md okunmalı.",
        "",
        "[measured]",
        f"events = {len(events)}",
        f"material_z2 = {sum(1 for e in events if e.z1 is not None and abs(e.z1) >= MATERIAL_Z)}",
        "",
    ]
    if best is not None:
        lines += [
            "[measured.best_variant]",
            f"threshold = {best.threshold}",
            f"max_per_day = {best.max_per_day if best.max_per_day is not None else 0}",
            f"precision = {best.precision:.4f}",
            f"missed_material = {best.missed_material}",
            f"pushed = {best.pushed}",
            "",
        ]
    else:
        lines += [
            "# Ölçüm yapılamadı: hiçbir varyantta push üretilmedi.",
            "# Önce örneklemi/tutar kaynağını düzelt, sonra eşik tartışılır.",
            "",
        ]
    lines += [
        "[measured.current]",
        f"push_threshold = {cfg.push_threshold}",
        f"digest_threshold = {cfg.digest_threshold}",
        "",
    ]
    MEASURED.parent.mkdir(parents=True, exist_ok=True)
    MEASURED.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------- akış


def run(*, dry_run: bool, limit: int | None, bodies: bool, max_bodies: int) -> int:
    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    tickers = read_universe()
    print(f"Evren: {len(tickers)} hisse · {'DRY-RUN' if dry_run else 'GERÇEK VERİ'}")

    absent: list[str] = []
    skip_counts: dict[str, int] = {}
    body_state: dict[str, int] | None = None
    body_provider = None

    if dry_run:
        html = (FIXTURES / "member_history_mtrks.html").read_text(encoding="utf-8")
        rows = parse_history(html, today=date(2026, 11, 17))
        members = [
            MemberData(
                ticker="MTRKS",
                oid="8acae2c575a1b5b60177de29c7177130",
                rows=rows,
                fundamentals=FundamentalRow(
                    ticker="MTRKS", period_end="2026-06-30", revenue_ttm=3.2e9,
                    net_income_ttm=0.4e9, source="dry-run",
                ),
            )
        ]
        stocks, market = synthetic_prices(members)
    else:
        from bistman.sources.kap import KapClient

        members, absent = asyncio.run(collect(tickers, limit=limit))
        print("Fiyatlar indiriliyor...")
        stocks, market = load_prices(members)
        if market is None:
            print("HATA: XU100 serisi olmadan CAR hesaplanamaz.")
            return 2

    rows_by_member: dict[str, list[HistoryRow]] = {}
    for member in members:
        if not member.rows:
            continue
        kept, skipped = select_rows(member.rows, member.ticker)
        for name, count in skipped.items():
            skip_counts[name] = skip_counts.get(name, 0) + count
        if kept:
            rows_by_member[member.ticker] = kept

    fundamentals = {m.ticker: m.fundamentals for m in members if m.fundamentals}
    resolver = TickerResolver.from_pairs(
        [(m.ticker, m.rows[0].company_title if m.rows else m.ticker) for m in members]
    )

    if dry_run:
        print("Hat çalışıyor (gövde yok: ağ kapalı)...")
        results = asyncio.run(
            build_scored_events(
                rows_by_member, cfg=cfg, fundamentals=fundamentals,
                resolver=resolver, notifier=FakeNotifier(),
            )
        )
    else:
        from bistman.sources.kap import KapClient

        async def pipeline_with_bodies():
            async with KapClient() as kap:
                provider = make_body_provider(kap, max_bodies=max_bodies) if bodies else None
                out = await build_scored_events(
                    rows_by_member, cfg=cfg, fundamentals=fundamentals,
                    resolver=resolver, notifier=FakeNotifier(), body_provider=provider,
                )
                return out, (provider.state if provider is not None else None)

        print("Hat çalışıyor (tutar için gövde indiriliyor)...")
        results, body_state = asyncio.run(pipeline_with_bodies())
        if body_state:
            print(
                f"Gövde: {body_state['fetched']} indirildi · "
                f"{body_state['cached']} önbellek · {body_state['failed']} başarısız"
            )

    events, reasons = attach_event_study(results, stocks, market)
    cov = coverage(rows_by_member)
    stats = score_stats(events, cfg)
    reports = sweep(events, thresholds=THRESHOLDS, max_per_day=BUDGETS)

    report_text = render_report(
        members=members, events=events, reasons=reasons, skips=skip_counts,
        reports=reports, cov=cov, absent=absent, stats=stats,
        bodies=body_state, dry_run=dry_run, cfg=cfg,
    )
    if dry_run:
        print(report_text)
        print("DRY-RUN: gerçek rapor ve ölçüm dosyalarına yazılmadı.")
        return 0

    print("\n=== SKOR DAĞILIMI ===")
    if stats.get("n", 0):
        print(
            f"  olay {stats['n']:.0f} · min {stats['min']:.2f} · medyan {stats['median']:.2f} "
            f"· max {stats['max']:.2f} · ≥digest {stats['ge_digest']:.0f} "
            f"· ≥push {stats['ge_push']:.0f}"
        )
    print("\n=== EŞİK × BÜTÇE ===")
    for report in reports:
        print("  " + report.as_row())

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report_text, encoding="utf-8")
    write_measured(events, reports, cfg)

    print(f"\nRapor : {REPORT}")
    print(f"Ölçüm : {MEASURED}")
    if reasons:
        print("Hesaplanamayan: " + ", ".join(f"{n}={c}" for n, c in reasons))
    if len(events) < 30:
        print("\nUYARI: örneklem küçük — eşik seçimi için yeterli değil, mekanizma doğrulaması.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Faz 4 gerçek kalibrasyon")
    parser.add_argument("--dry-run", action="store_true", help="ağ yok: fixture + sentetik fiyat")
    parser.add_argument("--limit", type=int, default=None, help="ilk N hisse")
    parser.add_argument("--no-bodies", action="store_true", help="gövde indirme (skorlar düşük kalır)")
    parser.add_argument("--max-bodies", type=int, default=400, help="en fazla kaç gövde indirilsin")
    args = parser.parse_args()
    return run(
        dry_run=args.dry_run,
        limit=args.limit,
        bodies=not args.no_bodies,
        max_bodies=args.max_bodies,
    )


if __name__ == "__main__":
    raise SystemExit(main())
