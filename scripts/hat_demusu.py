"""Uçtan uca hat demosu — GERÇEK KAP akışıyla (17.11.2026 canlı kaydı).

Akış: fixture (gerçek list/light yanıtı) -> ayrıştır -> triyaj -> unvan eşleme
      -> oranlar -> kural skor -> (sahte) Telegram.

Ağ yok, DB yok, LLM yok. Ama hattın tamamı bu.

Kullanım:
    .venv/bin/python scripts/hat_demusu.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.notify import FakeNotifier  # noqa: E402
from bistman.pipeline import process_batch, summarize  # noqa: E402
from bistman.scoring import ScoringConfig  # noqa: E402
from bistman.sources.base import FundamentalRow  # noqa: E402
from bistman.sources.kap import parse_light  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "kap" / "list_light_sample.json"

# Gerçek KAP ünvanları -> kodlar (prod'da companies() Excel'inden gelir)
RESOLVER = TickerResolver.from_pairs(
    [
        ("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."),
        ("GOLDA", "GOLDA GIDA SANAYİ VE TİCARET A.Ş."),
        ("MARGUN", "MARGÜN ENERJİ ÜRETİM SANAYİ VE TİCARET A.Ş."),
        ("DENZB", "DENİZBANK A.Ş."),
    ]
)

FUNDAMENTALS = {
    "MTRKS": FundamentalRow(
        ticker="MTRKS", period_end="2026-09-30", revenue_ttm=3_200_000_000.0, source="demo"
    ),
    "GOLDA": FundamentalRow(
        ticker="GOLDA", period_end="2026-09-30", revenue_ttm=8_500_000_000.0, source="demo"
    ),
    "MARGUN": FundamentalRow(
        ticker="MARGUN", period_end="2026-09-30", revenue_ttm=1_400_000_000.0, source="demo"
    ),
}

STATUS_ICON = {
    "scored": "✓",
    "filtered_routine": "·",
    "filtered_mechanical": "·",
    "fundamentals_refresh": "↻",
    "unresolved_ticker": "?",
    "filtered_fund": "·",
    "no_fundamentals": "!",
}


def main() -> int:
    parsed = parse_light(FIXTURE.read_bytes())
    print(f"KAP akışı: {len(parsed)} bildirim"
          + (f" ({len(parsed.rejected)} satır reddedildi)" if parsed.rejected else ""))
    print("-" * 100)

    notifier = FakeNotifier()
    results = asyncio.run(
        process_batch(
            parsed.items,
            resolver=RESOLVER,
            fundamentals=FUNDAMENTALS,
            cfg=ScoringConfig.load(),
            notifier=notifier,
        )
    )

    for item in results:
        action = item.decision.action if item.decision else "-"
        score = f"{item.decision.push_score:5.2f}" if item.decision else "  -  "
        ticker = item.ticker or "  ?  "
        title = item.disclosure.title[:38]
        print(
            f"{STATUS_ICON.get(item.status, ' ')} {item.disclosure.index} "
            f"{ticker:6} {item.status:22} {action:14} {score}  "
            f"{item.disclosure.subject[:34]:34} | {title}"
        )

    print("-" * 100)
    counts = summarize(results)
    print("Özet:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    print(f"\nGönderilen bildirim(ler) — {notifier.count} adet:")
    for key, message in notifier.sent:
        print(f"\n[{key}]\n{message}")

    if notifier.count == 0:
        print("  (bu 9 kayıtta eşiği geçen yok — gürültü oranı yüksek, beklenen)")

    print("\nNot: '·' = elendi, '↻' = veri tazeleme, '?' = unvan eşleşmedi, '✓' = skorlandı")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
