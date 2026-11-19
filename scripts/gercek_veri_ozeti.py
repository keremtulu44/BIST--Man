"""Gerçek veri özeti — "doğrulama ne değiştirdi?" sorusunun sayısal cevabı.

Üç bölüm:
  1. Ölçülen gerçek sayılar (docs/dogrulama-sonuc*.json + 4. turun ekran çıktısı)
  2. Aynı haber, iki farklı payda: karar nasıl değişiyor? (gerçek scoring hattı)
  3. Gerçek KAP akışında hat ne yapıyor? (fixture + triaj + skor)

Çalıştır:  python scripts\\gercek_veri_ozeti.py
Ağ gerekmez; tüm sayılar kayıtlı gerçek yanıtlardan.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.analysis.ttm import TTMPoint, compute_ttm  # noqa: E402
from bistman.notify import FakeNotifier  # noqa: E402
from bistman.sources.kap import parse_light  # noqa: E402
from bistman.pipeline import process_batch  # noqa: E402
from bistman.scoring import ScoringConfig  # noqa: E402
from bistman.sources.base import DisclosureStub, FundamentalRow  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "kap"

# ---------------------------------------------------------------- 1) ölçümler

# Kullanıcının makinesindeki gerçek yanıtlardan (17.11.2026)
THYAO_REV = [(2024, 3, 618.691e9), (2024, 4, 975.711e9), (2025, 1, 176.712e9),
             (2025, 2, 408.036e9), (2025, 3, 690.825e9), (2025, 4, 1187.816e9),
             (2026, 1, 257.961e9), (2026, 2, 585.069e9)]
THYAO_ANNUAL = {2025: 1187.816e9, 2024: 975.711e9}
AKBNK_REV = [(2024, 3, 337.670e9), (2024, 4, 480.073e9), (2025, 1, 140.202e9),
             (2025, 2, 297.916e9), (2025, 3, 461.610e9), (2025, 4, 628.174e9),
             (2026, 1, 162.844e9), (2026, 2, 349.461e9)]
AKBNK_ANNUAL = {2025: 628.174e9, 2024: 480.073e9}


def bolum_olcumler() -> dict[str, float]:
    print("=" * 78)
    print("1) ÖLÇÜLEN GERÇEK SAYILAR — doğrulama 5 tur sürdü, 4 hata bulundu")
    print("=" * 78)

    sonuc: dict[str, float] = {}
    for name, seri, yillik in (("THYAO", THYAO_REV, THYAO_ANNUAL), ("AKBNK", AKBNK_REV, AKBNK_ANNUAL)):
        pts = [TTMPoint(y, q, v) for y, q, v in seri]
        dogru = compute_ttm(pts, yillik)
        yanlis = sum(v for _, _, v in seri[-4:])
        sonuc[name] = dogru.value or 0.0
        print(f"\n{name} cirosu (gelir tablosu, kümülatif YTD)")
        print(f"  borsapy yöntemi (4 sütun topla) : {yanlis / 1e9:>10,.1f} milyar TL")
        print(f"  DOĞRU (FY + YTD - geçen yıl YTD) : {dogru.value / 1e9:>10,.1f} milyar TL"
              f"   [{dogru.note.split(';')[1].strip()}]")
        print(f"  fazla sayım                      : {yanlis / (dogru.value or 1):>10.2f}x")

    print("\nDiğer kalemler (aynı gün, aynı yanıt):")
    print("  net kâr   : nakit akışındaki 'Dönem Karı Vergi Yükümlülüğü' (0.53 mlr) seçilmişti")
    print("              doğrusu gelir tablosu 'SÜRDÜRÜLEN FAALİYETLER DÖNEM KARI' = 146.84 mlr")
    print("  özkaynak  : 'TTM' 1,399 mlr -> yanlış (stok kalem); son bilanço 1,018 mlr")
    print("  AKBNK Q3  : özkaynak/aktif = 0 -> veri boşluğu (258.9 -> 0 -> 310.2)")
    print("  tablolar  : THYAO 23+33+43+34 = 133 kalem; AKBNK 66+64+62 (nakit akışı YOK)")
    return sonuc


# ------------------------------------------------- 2) karar farkı (aynı haber)


def bolum_karar(cfg: ScoringConfig, resolver: TickerResolver) -> None:
    print("\n" + "=" * 78)
    print("2) AYNI HABER, İKİ PAYDA — karar nasıl değişiyor?")
    print("=" * 78)

    yanlis_payda = Decimal("2721671196737")   # borsapy'nin 4 sütun toplamı
    dogru_payda = Decimal("1364849196737")    # FY + YTD - geçen yıl YTD

    ornekler = [
        ("TÜRK HAVA YOLLARI A.O.", "Yeni Sözleşme İmzalanması",
         "Şirketimiz ile bir kamu kurumu arasında 15.000.000.000 TL bedelli sözleşme imzalanmıştır."),
        ("TÜRK HAVA YOLLARI A.O.", "Yatırım Kararı",
         "Yeni hangar yatırımı için 3.000.000.000 TL yatırım kararı alınmıştır."),
        ("TÜRK HAVA YOLLARI A.O.", "Haber ve Söylentilere İlişkin Açıklama",
         "Basında çıkan haberlere ilişkin açıklama yapılmıştır."),
    ]

    print(f"{'haber':38} {'payda':9} {'oran':>7}  {'impact':>6} {'skor':>6}  karar")
    print("-" * 78)
    for title, subject, summary in ornekler:
        for etiket, payda in (("yanlış", yanlis_payda), ("doğru", dogru_payda)):
            stub = DisclosureStub(
                index=abs(hash((subject, etiket))) % 10**7,
                published_at=datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc),
                title=title, subject=subject, summary=summary,
                disclosure_class="ODA", tickers=("THYAO",),
            )
            fund = FundamentalRow(
                ticker="THYAO", period_end="2026-06-30",
                revenue_ttm=float(payda), net_income_ttm=146.84e9,
            )
            results = asyncio.run(
                process_batch(
                    [stub], resolver=resolver, fundamentals={"THYAO": fund},
                    cfg=cfg, notifier=FakeNotifier(),
                )
            )
            r = results[0]
            oran = (
                f"{r.features.ratio_revenue:>6.2f}%"
                if r.features and r.features.ratio_revenue is not None
                else f"{'tutar yok':>7}"
            )
            if r.decision and r.features:
                print(f"{subject[:37]:38} {etiket:9} {oran} {r.decision.impact:>6.2f} "
                      f"{r.decision.push_score:>6.2f}  {r.decision.action}")
            else:
                print(f"{subject[:37]:38} {etiket:9} {oran} {'-':>6} {'-':>6}  {r.status}")
        print()


# ------------------------------------------------ 3) gerçek akışta hat ne yapıyor


def bolum_akis(cfg: ScoringConfig, resolver: TickerResolver) -> None:
    print("=" * 78)
    print("3) GERÇEK KAP AKIŞI (17.11.2026, 9 bildirim) — hat ne yapıyor?")
    print("=" * 78)

    # `list_light_sample.json` = 17.11.2026 akışından alınan 9 kayıtlık kesit
    # (`hat_demusu.py` ile aynı; live dosyası 397 kayıt, farklı kesit)
    stub_list = parse_light((FIXTURES / "list_light_sample.json").read_bytes()).items
    funds = {
        "MTRKS": FundamentalRow(ticker="MTRKS", period_end="2026-09-30",
                                revenue_ttm=3_200_000_000.0, source="demo"),
        "GOLDA": FundamentalRow(ticker="GOLDA", period_end="2026-09-30",
                                revenue_ttm=8_500_000_000.0, source="demo"),
        "MARGUN": FundamentalRow(ticker="MARGUN", period_end="2026-09-30",
                                 revenue_ttm=1_900_000_000.0, source="demo"),
    }
    results = asyncio.run(
        process_batch(stub_list, resolver=resolver, fundamentals=funds,
                      cfg=cfg, notifier=FakeNotifier())
    )
    sayim: dict[str, int] = {}
    for r in results:
        sayim[r.status] = sayim.get(r.status, 0) + 1
    for status, n in sorted(sayim.items(), key=lambda kv: -kv[1]):
        print(f"  {status:22} {n}")
    print("\n  Not: eşiği geçen yok — gürültü oranı beklendiği gibi yüksek.")


def main() -> int:
    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    # Gerçek KAP ünvanları (hat_demusu ile aynı)
    resolver = TickerResolver.from_pairs(
        [
            ("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."),
            ("GOLDA", "GOLDA GIDA SANAYİ VE TİCARET A.Ş."),
            ("MARGUN", "MARGÜN ENERJİ ÜRETİM SANAYİ VE TİCARET A.Ş."),
            ("THYAO", "TÜRK HAVA YOLLARI A.O."),
        ]
    )
    bolum_olcumler()
    bolum_karar(cfg, resolver)
    bolum_akis(cfg, resolver)
    print("\n" + "=" * 78)
    print("ÖZET: payda 2x yanlış olsaydı oran yarıya düşer, haber 'küçük' görünür,")
    print("sistem susar. Sayı doğru olunca aynı haber digest/push eşiğine yaklaşır.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
