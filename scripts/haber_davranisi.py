"""HABER DAVRANIŞI — sistem bir habere ne yapıyor? (ağ yok, anında çalışır)

Üç bölüm:

  1. **Gerçek fixture dağılımı** — 89 gerçek MTRKS bildirimi hangi kapıya düştü.
  2. **Temsili haberler × iki bilanço** — aynı haber, büyük ve küçük şirkette.
  3. **Tuzak metinler** — tutar çıkarımının yanıldığı gerçek cümle kalıpları.

Kullanım:

    python scripts\\haber_davranisi.py
    python scripts\\haber_davranisi.py --verbose     # her kararın gerekçesi
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.features import amount_try_from_text, build_features  # noqa: E402
from bistman.parsing.kap_table import parse_history  # noqa: E402
from bistman.scoring import ScoringConfig, score  # noqa: E402
from bistman.triage import route  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "kap" / "member_history_mtrks.html"

# Bilanço iki uçtan: dev şirket ve küçük şirket. Tutar sabit, karar değişiyor.
BOOKS: dict[str, int] = {
    "THYAO 1.36 trl": 1_364_849_196_737,
    "MTRKS 3.2 mlr": 3_200_000_000,
}

CORPUS: tuple[tuple[str, str, str], ...] = (
    ("öda", "Yeni Sözleşme İmzalanması", "Şirketimiz 15 milyar TL bedelli sözleşme imzalamıştır."),
    ("öda", "Yatırım Kararı", "Şirketimiz 3 milyar TL yatırım kararı almıştır."),
    ("öda", "Temettü Dağıtımı", "Şirketimiz 500 milyon TL temettü dağıtacaktır."),
    ("öda", "Bedelli Sermaye Artırımı", "Şirketimiz 1 milyar TL bedelli sermaye artırımı yapacaktır."),
    ("öda", "Vergi Cezası", "Şirketimize 250 milyon TL vergi cezası kesilmiştir."),
    ("öda", "Dava Açılması", "Şirketimiz aleyhine 300 milyon TL'lik dava açılmıştır."),
    ("öda", "SPK İşlem Yasağı", "Şirketimiz paylarında işlem yasağı uygulanacaktır."),
    ("öda", "Konkordato Talebi", "Şirketimiz konkordato talebinde bulunmuştur."),
    ("öda", "Üretime Ara Verilmesi", "Yangın nedeniyle üretime ara verilmiştir."),
    ("öda", "Finansal Rapor", "Şirketimiz 2026/9 net kârı 2 milyar TL olarak açıklanmıştır."),
    ("öda", "Özel Durum Açıklaması (Genel)", "Şirketimiz 800 milyon TL sipariş aldığını bildirmiştir."),
    ("öda", "Haber ve Söylentilere İlişkin Açıklama", "Şirketimiz hakkında çıkan haberlere ilişkin açıklama."),
    ("öda", "Pay Alım Satım Bildirimi", "Fon portföyünde değişiklik."),
    ("dkb", "Devre Kesici", "Sermaye Piyasası Kurulu kararı."),
    ("öda", "Genel Kurul İşlemlerine İlişkin Bildirim", "Genel kurul yapıldı."),
)

TRAPS: tuple[str, ...] = (
    "Toplam yatırım tutarı 10 milyar TL olup şirketimizin payı 2 milyar TL'dir.",
    "Şirketimiz 1.250.000.000 TL ve 750.000.000 TL tutarında iki sözleşme imzalamıştır.",
    "Sermaye 250.000.000 TL'den 500.000.000 TL'ye çıkarılacaktır.",
    "Sözleşme bedeli 500 milyon dolar olarak belirlenmiştir.",
    "Bedeli 45.000.000 Avro'dur.",
    "Şirketimiz 2 milyar TL'lik yatırımın %25'ini üstlenecektir.",
)


def section_fixture() -> None:
    rows = parse_history(FIXTURE.read_text(encoding="utf-8"), today=date(2026, 11, 17))
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        buckets[route(row.disclosure_class, row.subject, row.summary).value][row.subject[:58]] += 1
    print("=" * 78)
    print(f"1) GERÇEK FIXTURE — {len(rows)} bildirim (MTRKS, 1 yıl)")
    print("=" * 78)
    order = ("pipeline", "fundamentals_refresh", "mechanical", "routine")
    for name in order:
        subjects = buckets.get(name, Counter())
        print(f"\n  {name} ({sum(subjects.values())})")
        for subject, count in subjects.most_common():
            print(f"    {count:3d}  {subject}")


def section_corpus(cfg: ScoringConfig, verbose: bool) -> None:
    print("\n" + "=" * 78)
    print("2) TEMSİLİ HABERLER — aynı haber, farklı bilanço")
    print("=" * 78)
    header = f"  {'haber':34s} {'tutar':11s} → " + "".join(f"{b:22s}" for b in BOOKS)
    print(header)
    for cls, subject, summary in CORPUS:
        route_name = route(cls, subject, summary).value
        text = f"{subject} {summary}"
        amount, flags = amount_try_from_text(text)
        cells = []
        details = []
        for label, revenue in BOOKS.items():
            features = build_features(
                ticker="X", amount_try=amount,
                revenue_ttm=Decimal(str(revenue)), extra_flags=flags,
            )
            decision = score(features, subject, cfg)
            ratio = (
                f"%{features.primary_ratio:.0f}" if features.primary_ratio is not None else "—"
            )
            cells.append(f"{ratio:>5s} {decision.action[:10]:11s}{decision.push_score:5.2f}")
            details.append((label, features, decision))
        amount_txt = (
            f"{amount / Decimal(1_000_000):.0f} mn" if amount is not None else "yok"
        )
        print(f"  {subject[:32]:34s} {amount_txt:11s} → " + "".join(f"{c:22s}" for c in cells))
        if route_name != "pipeline":
            print(f"  {'':34s} {'':11s}   ↳ triyaj: {route_name}")
        if verbose:
            for label, features, decision in details:
                print(f"      {label}: " + " | ".join(decision.reasons))
                if features.flags:
                    print(f"        bayrak: {', '.join(features.flags)}")


def section_traps() -> None:
    print("\n" + "=" * 78)
    print("3) TUZAK METİNLER — tutar çıkarımı nerede yanılıyor")
    print("=" * 78)
    for text in TRAPS:
        amount, flags = amount_try_from_text(text)
        shown = f"{amount:,.0f} TL" if amount is not None else "—"
        flag_txt = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {text[:66]:68s} {shown:>18s}{flag_txt}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sistem haberlere nasıl davranıyor?")
    parser.add_argument("--verbose", action="store_true", help="karar gerekçelerini yaz")
    args = parser.parse_args()
    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    section_fixture()
    section_corpus(cfg, args.verbose)
    section_traps()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
