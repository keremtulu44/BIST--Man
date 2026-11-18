"""METİNLEŞTİRME · SIRALAMA · BİLANÇO KULLANIMI — genel kontrol (ağ yok)

Üç soru:

  A) Metinden sayı çıkıyor mu? 89 gerçek bildirimde tutar bulma oranı nedir,
     nereden geliyor, ne kaybediliyor?
  B) Normalizasyon sağlam mı? Türkçe unvan/para kalıplarında nerede kırılıyor?
  C) Veri önem sırasına göre sıralanıyor mu? Canlı hatta sıralama var mı?
  D) Bilançonun kaç kalemi karara giriyor? Hangileri hesaplanıp atılıyor?

Kullanım:

    python scripts\\metin_ve_bilanco_denetimi.py
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.features import amount_try_from_text, build_features  # noqa: E402
from bistman.parsing.kap_table import parse_history  # noqa: E402
from bistman.parsing.money import find_amounts  # noqa: E402
from bistman.parsing.text import name_norm, tr_lower  # noqa: E402
from bistman.scoring import ScoringConfig, score  # noqa: E402
from bistman.sources.base import FundamentalRow  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "kap" / "member_history_mtrks.html"
SEP = "=" * 78

TITLES = (
    "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.",
    "MATRIKS FINANSAL TEKNOLOJI A.S.",
    "TÜRKİYE İŞ BANKASI A.Ş.",
    "TÜRKİYE IS BANKASI A.S.",
    "IŞIKLAR ENERJİ A.Ş.",
    "ŞİŞECAM T.A.Ş.",
    "ASELSAN ELEKTRONİK SANAYİ VE TİCARET A.Ş.",
    "KARDEMİR KARABÜK DEMİR ÇELİK SANAYİ VE TİCARET A.Ş.",
    "İŞ PORTFÖY YÖNETİMİ A.Ş.",
    "GARANTİ BBVA A.O.",
    "TÜRK HAVA YOLLARI TEKNİK A.Ş.",
)

MONEY_TEXTS = (
    "Sözleşme bedeli 15 milyar TL'dir.",
    "Toplam 1.250.000.000 TL yatırım yapılacaktır.",
    "Bedel 500 milyon dolar + KDV'dir.",
    "Yatırımın %25'i şirketimiz tarafından karşılanacaktır.",
    "Hisse başına 2,35 TL temettü dağıtılacaktır.",
    "Sermaye 250.000.000 TL'den 500.000.000 TL'ye çıkarılacaktır.",
    "Kira geliri yıllık 12.000.000 TL olarak belirlenmiştir.",
    "Şirketimiz 3. çeyrekte 4,2 milyar TL kâr açıklamıştır.",
)


# --------------------------------------------------------------------- A


def section_text_to_number() -> None:
    rows = parse_history(FIXTURE.read_text(encoding="utf-8"), today=date(2026, 11, 17))
    print(SEP)
    print(f"A) METİN → SAYI — {len(rows)} gerçek bildirim")
    print(SEP)

    with_amount = 0
    sources: Counter[str] = Counter()
    examples: list[str] = []
    for row in rows:
        text = " ".join(x for x in (row.subject, row.summary) if x)
        amount, flags = amount_try_from_text(text)
        if amount is not None:
            with_amount += 1
            where = "özet" if row.summary else "konu"
            sources[where] += 1
            if len(examples) < 3:
                examples.append(f"{amount:,.0f} TL ← {row.summary[:60] or row.subject[:60]}")

    print(f"\n  tutar bulunan satır: {with_amount}/{len(rows)} "
          f"(%{with_amount / len(rows) * 100:.1f})")
    if sources:
        for where, count in sources.items():
            print(f"    kaynak {where}: {count}")
    for example in examples:
        print(f"    örnek: {example}")
    if with_amount == 0:
        print("\n  → Hiçbir satırda tutar yok: geçmiş tablosu tutar TAŞIMIYOR.")
        print("    Tutar yalnız bildirim gövdesinde; ölçüm için gövde indirmek şart.")

    multi = sum(1 for row in rows if len(find_amounts(row.summary or "")) > 1)
    print(f"\n  birden çok tutar içeren satır: {multi} (bu satırlarda 'en büyük' seçilir)")


def section_resolution() -> None:
    """Unvan → kod: canlı akış ticker vermiyor, atıf tamamen metne kalıyor."""
    from bistman.parsing.text import symbols_in_title
    from bistman.pipeline import _resolve_ticker
    from bistman.sources.base import DisclosureStub
    from bistman.ticker_map import TickerResolver
    from bistman.triage import route

    rows = parse_history(FIXTURE.read_text(encoding="utf-8"), today=date(2026, 11, 17))
    resolver = TickerResolver.from_pairs([("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")])
    stubs = [
        DisclosureStub(
            index=row.index, published_at=row.published_at, title=row.company_title,
            subject=row.subject, summary=row.summary or None,
            disclosure_class=row.disclosure_class, tickers=(),
        )
        for row in rows
    ]

    print("\n  unvan eşleşmesi (tüm satırlar):")
    by_title = sum(1 for stub in stubs if resolver.resolve(stub.title).ticker)
    print(f"    çözülen {by_title}/{len(stubs)} · çözülemeyen {len(stubs) - by_title}")

    pipeline = [
        stub for stub in stubs
        if route(stub.disclosure_class, stub.subject, stub.summary).value == "pipeline"
    ]
    resolved = sum(1 for stub in pipeline if _resolve_ticker(stub, resolver))
    print("\n  ticker'ın GERÇEKTEN gerektiği satırlar (triyaj=pipeline):")
    print(f"    {len(pipeline)} satır · çözülen {resolved} · çözülemeyen {len(pipeline) - resolved}")
    unresolved = [stub for stub in pipeline if not _resolve_ticker(stub, resolver)]
    for stub in unresolved[:3]:
        count = len(symbols_in_title(stub.title))
        print(f"    - {stub.title[:56]:56s} ({count} sembol)")
    print("\n  → Çözülemeyenlerin hepsi ÇOK sembollü (fon akışı / piyasa geneli):")
    print("    tek hisseye yazmak tahmin olur, yazmıyoruz. Tek sembollü platform")
    print("    başlığı için başlık-sembolü yedeği eklendi (bu fixture'da kazanç 0).")
    print("  → Triyaj ticker'dan ÖNCE çalışıyor: filtrelenen satırlar hiç ticker almıyor.")
    print("    '3+ devre kesici' sayımı yapılacaksa bu sıra değişmeli.")


# --------------------------------------------------------------------- B


def section_normalization() -> None:
    print("\n" + SEP)
    print("B) NORMALİZASYON — Türkçe unvan ve para kalıpları")
    print(SEP)
    print("\n  unvan → eşleştirme anahtarı")
    print(f"  {'girdi':44s} {'name_norm çıktısı':36s} not")
    seen: dict[str, list[str]] = {}
    for title in TITLES:
        key = name_norm(title)
        seen.setdefault(key, []).append(title)
        note = ""
        if len(key) < 4:
            note = "KISA: hukuki ek atma fazla mı?"
        if any(ch in key for ch in "ışğüöç"):
            note = "TR karakter kaldı (ASCII katlama eksik?)"
        print(f"  {title[:42]:44s} {key[:34]:36s} {note}")

    print("\n  aynı anahtara düşen farklı yazımlar (istenen davranış):")
    for key, titles in seen.items():
        if len(titles) > 1:
            print(f"    {key!r} ← {len(titles)} yazım: {', '.join(t[:22] for t in titles)}")

    print("\n  Türkçe küçük harf kontrolü:")
    for word in ("İŞ BANKASI", "IŞIK", "İSTANBUL", "ÇĞİÖŞÜ", "İı"):
        print(f"    tr_lower({word!r:16s}) = {tr_lower(word)!r}")

    print("\n  para kalıpları → tutar")
    print(f"  {'metin':56s} {'tutar':>18s} bayrak")
    for text in MONEY_TEXTS:
        amount, flags = amount_try_from_text(text)
        shown = (f"{amount.normalize():,.2f}" if amount is not None and amount < 1000
                 else f"{amount:,.0f}" if amount is not None else "—")
        print(f"  {text[:54]:56s} {shown:>18s} {', '.join(flags)}")


# --------------------------------------------------------------------- C


def section_ordering(cfg: ScoringConfig) -> int:
    print("\n" + SEP)
    print("C) SIRALAMA — canlı hatta veri önem sırasına diziliyor mu?")
    print(SEP)

    import asyncio

    from bistman.backfill import build_scored_events, select_rows
    from bistman.notify import FakeNotifier
    from bistman.ticker_map import TickerResolver

    rows = parse_history(FIXTURE.read_text(encoding="utf-8"), today=date(2026, 11, 17))
    kept, _skips = select_rows(rows, "MTRKS")
    fundamentals = {
        "MTRKS": FundamentalRow(
            ticker="MTRKS", period_end="2026-06-30",
            revenue_ttm=3.2e9, net_income_ttm=0.4e9, equity=1.1e9, source="denetim",
        )
    }
    results = asyncio.run(
        build_scored_events(
            {"MTRKS": kept}, cfg=cfg, fundamentals=fundamentals,
            resolver=TickerResolver.from_pairs([("MTRKS", kept[0].company_title)]),
            notifier=FakeNotifier(),
        )
    )
    scored = [r for r in results if r.decision is not None]
    print(f"\n  {len(results)} sonuç, {len(scored)} skorlu")
    print("\n  ÇIKIŞ SIRASI (KAP yayın sırası) —  ilk 6:")
    for item in results[:6]:
        score_txt = f"{item.decision.push_score:.2f}" if item.decision else "—"
        print(f"    {item.disclosure.published_at:%Y-%m-%d %H:%M}  skor {score_txt:>5s}  "
              f"{item.status:22s} {item.disclosure.subject[:34]}")
    top = sorted(scored, key=lambda r: -r.decision.push_score)[:6]
    print("\n  ÖNEM SIRASI (skora göre) — ilk 6:")
    for item in top:
        print(f"    {item.disclosure.published_at:%Y-%m-%d %H:%M}  skor "
              f"{item.decision.push_score:5.2f}  {item.disclosure.subject[:34]}")

    same = all(
        a.disclosure.index == b.disclosure.index for a, b in zip(results[: len(top)], top)
    )
    print(f"\n  iki sıralama aynı mı? {'EVET' if same else 'HAYIR'}")
    print("  kod: process_batch sonuçları giriş sırasıyla döndürür; `sorted(` çağrısı")
    print("  yalnız `analysis/calibration.py` (backtest) ve etiket eşlemede var.")
    print("  `digest` bir **etiket**; onu toplayan/sıralayan/gönderen kod yok.")
    return len(results)


# --------------------------------------------------------------------- D


def section_balance_sheet(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("D) BİLANÇO KULLANIMI — kaç kalem karara giriyor?")
    print(SEP)
    base = dict(revenue_ttm=Decimal("3200000000"), net_income_ttm=Decimal("400000000"))
    amount = Decimal("500000000")

    def decide(**kw):
        features = build_features(
            ticker="MTRKS", amount_try=amount,
            revenue_ttm=kw.get("revenue_ttm", base["revenue_ttm"]),
            net_income_ttm=kw.get("net_income_ttm", base["net_income_ttm"]),
            market_cap=kw.get("market_cap"),
        )
        decision = score(features, "Temettü Dağıtımı", cfg)
        return features, decision

    print("\n  Aynı haber (500 mn TL), kalemler tek tek değiştiriliyor:")
    print(f"  {'senaryo':34s} {'oran':>7s} {'impact':>7s} {'skor':>6s} karar")
    scenarios = (
        ("temel", {}),
        ("net kâr ×0.1", {"net_income_ttm": Decimal("40000000")}),
        ("net kâr ×10", {"net_income_ttm": Decimal("4000000000")}),
        ("net kâr negatif", {"net_income_ttm": Decimal("-900000000")}),
        ("piyasa değeri 5 mlr", {"market_cap": Decimal("5000000000")}),
        ("piyasa değeri 800 mn", {"market_cap": Decimal("800000000")}),
    )
    for label, kw in scenarios:
        features, decision = decide(**kw)
        ratio = features.primary_ratio
        ratio_txt = f"%{ratio:.2f}" if ratio is not None else "—"
        print(
            f"  {label:34s} {ratio_txt:>7s} {decision.impact:7.1f} "
            f"{decision.push_score:6.2f} {decision.action}"
        )

    print("\n  → net kâr ×0.1 / ×10 / negatif: skor DEĞİŞMİYOR.")
    print("    `ratio_net_income` hesaplanıyor ama `primary_ratio` onu kullanmıyor.")
    print("  → piyasa değeri verilirse oran ONU kullanıyor (PD > ciro), ama repoda")
    print("    PD üreten kaynak yok: canlı yolda `market_cap` hep None.")

    print("\n  kalem kalem karar:")
    rows = (
        ("revenue_ttm", "payda (ciro)", "KULLANILIYOR — her karar buna dayanıyor"),
        ("net_income_ttm", "hesaplanır, karara girmez", "ATIL") ,
        ("equity", "parse edilir, `features`'a hiç geçmez", "ATIL"),
        ("total_assets", "`isyatirim_labels` içinde çözülür, modele girmez", "ATIL"),
        ("market_cap", "parametre var, kaynak yok → hep None", "ÖLÜ KOD"),
        ("period_end", "taşınır, tazelik kontrolü yok", "KONTROLSÜZ"),
    )
    print(f"  {'kalem':18s} {'rol':44s} sonuç")
    for name, role, verdict in rows:
        print(f"  {name:18s} {role:44s} {verdict}")


def main() -> int:
    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    section_text_to_number()
    section_resolution()
    section_normalization()
    section_ordering(cfg)
    section_balance_sheet(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
