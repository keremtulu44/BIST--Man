"""GÜNLÜK ÖZET — sıralama + push/özet gönderimi (ağ yok).

İki bölüm:

  1. **Gerçek fixture** (89 MTRKS bildirimi): tutar olmadığı için gönderim yok.
     Bunu göstermek kasıtlı — "sessizlik" bir hata değil, ölçümün sonucu.
  2. **Senaryo** (açıkça sentetik tutarlar): sıralama, hisse başına günlük bütçe
     ve Telegram'a gidecek **gerçek metinler**. Sayılar demo; mekanizma gerçek.

Kullanım:

    python scripts\\gunluk_ozet.py
    python scripts\\gunluk_ozet.py --only-real     # yalnız gerçek fixture
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.backfill import build_scored_events, select_rows  # noqa: E402
from bistman.digest import dispatch, rank  # noqa: E402
from bistman.features import build_features  # noqa: E402
from bistman.notify import FakeNotifier  # noqa: E402
from bistman.parsing.kap_table import parse_history  # noqa: E402
from bistman.parsing.text import tr_lower  # noqa: E402
from bistman.pipeline import ProcessedDisclosure, render_message  # noqa: E402
from bistman.scoring import ScoringConfig, score  # noqa: E402
from bistman.sources.base import DisclosureStub, FundamentalRow  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402
from bistman.triage import Route  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "kap" / "member_history_mtrks.html"
DAY = date(2026, 11, 17)
SEP = "=" * 78
UTC = timezone.utc

# (ticker, konu, tutar, TTM ciro, saat) — tutarlar SENTETİK, kolay okunsun diye
SCENARIO: tuple[tuple[str, str, int, int, int], ...] = (
    ("MTRKS", "Yeni Sözleşme İmzalanması", 1_000_000_000, 3_200_000_000, 9),
    ("THYAO", "Yeni Sözleşme İmzalanması", 15_000_000_000, 1_364_849_196_737, 10),
    ("MTRKS", "Yatırım Kararı", 900_000_000, 3_200_000_000, 11),
    ("THYAO", "Temettü Dağıtımı", 2_000_000_000, 1_364_849_196_737, 12),
    ("MTRKS", "Temettü Dağıtımı", 500_000_000, 3_200_000_000, 13),
    ("MTRKS", "Satın Alma", 300_000_000, 3_200_000_000, 14),
    ("MTRKS", "Kapasite Artışı", 120_000_000, 3_200_000_000, 15),
    ("MTRKS", "Birleşme", 800_000_000, 3_200_000_000, 16),
)


def _money(amount: int) -> str:
    if amount % 1_000_000_000 == 0:
        return f"{amount // 1_000_000_000} milyar"
    return f"{amount // 1_000_000} milyon"


def real_results(cfg: ScoringConfig):
    rows = parse_history(FIXTURE.read_text(encoding="utf-8"), today=DAY)
    kept, skips = select_rows(rows, "MTRKS")
    fundamentals = {
        "MTRKS": FundamentalRow(
            ticker="MTRKS", period_end="2026-06-30",
            revenue_ttm=3.2e9, net_income_ttm=0.4e9, equity=1.1e9, source="demo",
        )
    }
    results = asyncio.run(
        build_scored_events(
            {"MTRKS": kept}, cfg=cfg, fundamentals=fundamentals,
            resolver=TickerResolver.from_pairs([("MTRKS", kept[0].company_title)]),
            notifier=FakeNotifier(),
        )
    )
    return results, skips


def scenario_results(cfg: ScoringConfig) -> list[ProcessedDisclosure]:
    out: list[ProcessedDisclosure] = []
    for i, (ticker, subject, amount, revenue, hour) in enumerate(SCENARIO, 1):
        stub = DisclosureStub(
            index=1000 + i,
            published_at=datetime(2026, 11, 17, hour, 0, tzinfo=UTC),
            title=f"{ticker} A.Ş.",
            subject=subject,
            summary=(
                f"Şirketimiz {_money(amount)} TL tutarında "
                f"{tr_lower(subject)} açıklamıştır."
            ),
            disclosure_class="öda",
            tickers=(ticker,),
        )
        features = build_features(
            ticker=ticker,
            amount_try=Decimal(amount),
            revenue_ttm=Decimal(revenue),
        )
        out.append(
            ProcessedDisclosure(
                disclosure=stub,
                route=Route.PIPELINE,
                status="scored",
                ticker=ticker,
                features=features,
                decision=score(features, subject, cfg),
            )
        )
    return out


def section_real(cfg: ScoringConfig) -> None:
    results, skips = real_results(cfg)
    ranked = rank(results, budget_per_ticker=cfg.max_push_per_ticker_per_day)
    print(SEP)
    print("1) GERÇEK FIXTURE — 89 MTRKS bildirimi")
    print(SEP)
    print(f"  sonuç {len(results)} · push {len(ranked.pushes)} · özet {len(ranked.digest)}"
          f" · satır seçimi {skips}")
    if not ranked.pushes and not ranked.digest:
        print("\n  → Gönderim YOK. Sebep tutar yok (geçmiş tablosu tutar taşımıyor),")
        print("    skorlar ~0. Bu bir hata değil: `kalibrasyon_gercek.py` gövdeleri")
        print("    indirdiğinde aynı sıralama gerçek sayılarla çalışır.")
    print()


def section_scenario(cfg: ScoringConfig) -> None:
    results = scenario_results(cfg)
    ranked = rank(results, budget_per_ticker=cfg.max_push_per_ticker_per_day)

    print(SEP)
    print("2) SENARYO — sentetik tutarlar, gerçek mekanizma")
    print(SEP)
    print(f"  günlük push bütçesi: hisse başına {cfg.max_push_per_ticker_per_day}\n")
    print("  GELİŞ sırası (KAP yayını)     │ ÖNEM sırası (skor, bütçe uygulanmış)")
    ordered = list(ranked.pushes) + list(ranked.digest)
    width = max(len(results), len(ordered))
    for i in range(width):
        left = ""
        if i < len(results):
            item = results[i]
            left = (f"{item.ticker:6s} {item.decision.push_score:5.2f} "
                    f"{item.disclosure.subject[:22]:24s}")
        right = ""
        if i < len(ordered):
            item = ordered[i]
            tag = "push" if item in ranked.pushes else "özet"
            mark = "*" if any(n.startswith("bütçe:") for n in item.notes) else " "
            right = (f"{tag}{mark} {item.ticker:6s} {item.decision.push_score:5.2f} "
                     f"{item.disclosure.subject[:22]}")
        print(f"  {left:34s} │ {right}")
    print("\n  * = bütçe aşıldı, push yerine özete indi (kaybolmadı)")

    notifier = FakeNotifier()
    report = asyncio.run(dispatch(results, notifier, day=DAY, cfg=cfg))
    print(f"\n  gönderim: {report.summary()} · mesaj {notifier.count}")

    for key, message in notifier.sent:
        print("\n" + "-" * 78)
        print(f"[{key}]")
        print(render_message_body(key, message))


def render_message_body(key: str, message: str) -> str:
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description="Günlük özet demosu")
    parser.add_argument("--only-real", action="store_true", help="yalnız gerçek fixture")
    args = parser.parse_args()

    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    section_real(cfg)
    if not args.only_real:
        section_scenario(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
