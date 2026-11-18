"""ÖRNEK VAKALAR — gerçek haber metni + bilanço + karar + Telegram mesajı.

Tek koşuda 12 vaka: her biri KAP'a düşen gerçekçi bir bildirim metni, gerçek
bilanço sayıları, ve sistemin **tam kararı** (oran → impact → emniyet → skor →
push/özet/sessiz). Push olanların Telegram mesajı da basılır.

Neden bu dosya: rakamlar tabloda değil, **vakada** görülsün. "Sistem habere ne
yapıyor" sorusunun cevabı iddia değil, çıktı.

Tutar kaynağı gerçek akıştaki gibi: **özet metninde tutar yok, gövdede var.**
Yani `body_provider` yolu da burada çalışıyor.

Bilançolar:
  THYAO, AKBNK — `docs/GERCEK-VERI-RAPORU.md`'deki doğrulanmış İş Yatırım
  değerleri (2026/6 TTM).
  MTRKS — küçük ölçekli şirket **varsayımı** (ciro 3.2 mlr); gerçek koşuda
  `isyatirim_client` ne derse o.

Kullanım:

    python scripts\\ornek_vakalar.py
    python scripts\\ornek_vakalar.py --only-actions     # yalnız gönderilenler
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.digest import dispatch, rank, render_digest  # noqa: E402
from bistman.notify import FakeNotifier  # noqa: E402
from bistman.pipeline import ProcessedDisclosure, process_batch, render_message  # noqa: E402
from bistman.scoring import ScoringConfig  # noqa: E402
from bistman.sources.base import DisclosureStub, FundamentalRow  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402

UTC = timezone.utc
DAY = date(2026, 11, 17)
SEP = "=" * 78

BALANCE_SHEETS: dict[str, FundamentalRow] = {
    "THYAO": FundamentalRow(
        ticker="THYAO", period_end="2026-06-30",
        revenue_ttm=1_364_849_196_737.0,
        net_income_ttm=140_670_766_848.0,
        equity=1_018_453_000_000.0,
        source="iş yatırım / gerçek",
    ),
    "AKBNK": FundamentalRow(
        ticker="AKBNK", period_end="2026-06-30",
        statement_type="bank",
        revenue_ttm=679_718_603_000.0,   # I. FAİZ GELİRLERİ
        net_income_ttm=66_774_061_000.0,
        equity=324_746_000_000.0,
        source="iş yatırım / gerçek",
    ),
    "MTRKS": FundamentalRow(
        ticker="MTRKS", period_end="2026-06-30",
        revenue_ttm=3_200_000_000.0,     # varsayım: küçük ölçekli şirket
        net_income_ttm=400_000_000.0,
        equity=1_100_000_000.0,
        source="varsayım",
    ),
}


@dataclass(frozen=True, slots=True)
class Case:
    ticker: str
    subject: str
    summary: str          # kullanıcı/akış özeti — burada tutar YOK (gerçek hayat)
    body: str             # bildirim gövdesi — tutar burada
    disclosure_class: str
    note: str             # bu vakanın neyi gösterdiği


CASES: tuple[Case, ...] = (
    Case(
        "THYAO", "Yeni Sözleşme İmzalanması",
        "Şirketimiz ile yurt dışı müşteri arasında sözleşme imzalanmıştır.",
        "İmzalanan sözleşmenin toplam bedeli 15 milyar TL'dir; teslimat 24 ayda "
        "tamamlanacaktır.",
        "öda", "Büyük şirkette 15 mlr: ciro 1.36 trilyon → %1 → digest",
    ),
    Case(
        "MTRKS", "Yeni Sözleşme İmzalanması",
        "Şirketimiz yeni bir sözleşme imzalamıştır.",
        "Sözleşme bedeli 1 milyar TL olup 12 ay içinde teslim edilecektir.",
        "öda", "Aynı haber küçük şirkette: %31 → push",
    ),
    Case(
        "MTRKS", "Yeni Sözleşme İmzalanması",
        "Şirketimiz yeni bir sözleşme imzalamıştır.",
        "Sözleşme bedeli 15 milyar TL'dir.",
        "öda", "Oran %469: payda hatası olabilir → emniyet ×0.25 → push_uncertain",
    ),
    Case(
        "MTRKS", "Temettü Dağıtımı",
        "Genel kurul temettü dağıtımını onaylamıştır.",
        "Dağıtılacak toplam temettü tutarı 500 milyon TL'dir.",
        "öda", "Nakit dönüşü: %16 → push",
    ),
    Case(
        "MTRKS", "Bedelli Sermaye Artırımı",
        "Şirketimiz bedelli sermaye artırımı yapacaktır.",
        "Artırılacak sermaye tutarı 1 milyar TL'dir (halka arz edilecektir).",
        "öda", "OLUMSUZ haber ama push: seyreltme, yön etiketi yok (F9)",
    ),
    Case(
        "MTRKS", "Vergi Cezası",
        "Şirketimize vergi cezası kesilmiştir.",
        "Kesilen ceza tutarı 250 milyon TL'dir; itiraz edilecektir.",
        "öda", "OLUMSUZ haber ve SESSİZ: ceza önseli −1.5 skoru düşürüyor",
    ),
    Case(
        "MTRKS", "Konkordato Talebi",
        "Şirketimiz konkordato talebinde bulunmuştur.",
        "Şirketimiz 18.11.2026 tarihinde konkordato talebinde bulunmuştur.",
        "öda", "Tutar yok; dar şirket gündemi kuralıyla özete girer",
    ),
    Case(
        "MTRKS", "Finansal Rapor",
        "Şirketimizin 2026/9 dönemi finansal raporu ekte sunulmuştur.",
        "Dönem net kârı 600 milyon TL; satış gelirleri 3,2 milyar TL'dir.",
        "fr", "Bilanço bildirimi: yenileme adayı; bu hat yenilemenin tamamlandığını söylemez",
    ),
    Case(
        "MTRKS", "Özel Durum Açıklaması (Genel)",
        "Şirketimiz sipariş aldığını duyurmuştur.",
        "Alınan siparişin bedeli 800 milyon TL'dir.",
        "öda", "Genel başlık: konu bilinmiyor, karar TAMAMEN büyüklükten",
    ),
    Case(
        "MTRKS", "Yeni Sözleşme İmzalanması",
        "Şirketimiz yurt dışı müşteri ile sözleşme imzalamıştır.",
        "Sözleşme bedeli 500 milyon dolar olup 18 ayda teslim edilecektir.",
        "öda", "DÖVİZ: imzalı sözleşme özette, özgün bedel korunur; kur yoksa oran yok",
    ),
    Case(
        "MTRKS", "Pay Bazında Devre Kesici Bildirimi",
        "MTRKS.E işlem sırasında devre kesici uygulaması devreye girmiştir.",
        "Pay bazında devre kesici uygulanmıştır.",
        "dkb", "Mekanik: kaydedilir, skorlanmaz (ama sayılmalı — F7)",
    ),
    Case(
        "MTRKS", "SPK İşlem Yasağı Nedeniyle Pay Duyurusu",
        "İşlem yasağı uygulanacaktır.",
        "SPK kararı gereği paylarda işlem yasağı uygulanacaktır.",
        "dkb", "Yapısal kod tek payı gösteriyor; yasağın kapsamı genişletilmeden özete girer",
    ),
)


def build_stubs() -> list[DisclosureStub]:
    return [
        DisclosureStub(
            index=5000 + i,
            published_at=datetime(2026, 11, 17, 9 + i % 9, 15, tzinfo=UTC),
            title=f"{case.ticker} A.Ş.",
            subject=case.subject,
            summary=case.summary,
            disclosure_class=case.disclosure_class,
            tickers=(case.ticker,),
        )
        for i, case in enumerate(CASES)
    ]


def make_body_provider():
    """Gerçek akış gibi: tutar özette yok, gövdede var."""
    by_index = {5000 + i: case.body for i, case in enumerate(CASES)}

    async def provider(stub: DisclosureStub) -> str:
        return by_index.get(stub.index, "")

    return provider


def resolve(case: Case, text: str) -> str:
    return text


def status_turkish(item: ProcessedDisclosure) -> str:
    mapping = {
        "scored": "skorlandı",
        "fundamentals_refresh": "bilanço yenileme",
        "filtered_mechanical": "mekanik (elenir)",
        "filtered_routine": "rutin (elenir)",
        "filtered_fund": "fon bildirimi",
        "unresolved_ticker": "hisse çözülemedi",
        "no_fundamentals": "bilanço yok",
    }
    return mapping.get(item.status, item.status)


def decision_turkish(item: ProcessedDisclosure) -> str:
    if item.planned_action is None:
        return "—"
    return {
        "push": "PUSH",
        "push_uncertain": "PUSH (belirsiz)",
        "digest": "özet",
        "silent": "sessiz",
    }[item.planned_action]


def reason_of(item: ProcessedDisclosure) -> str:
    """Tek satır gerekçe: hangi katman durdurdu / ne belirledi."""
    if item.brief:
        return item.brief
    if item.decision is None:
        return {
            "fundamentals_refresh": "Mali tablo bildirimi: skorlanmaz, yenileme için ayrılır",
            "filtered_mechanical": "Triyaj: mekanik (devre kesici vb.)",
            "filtered_routine": "Triyaj: rutin (form, genel kurul)",
            "filtered_fund": "Triyaj: fon/portföy bildirimi",
            "unresolved_ticker": "Unvan → hisse eşleşmedi",
            "no_fundamentals": "Bilanço yok: payda hesaplanamaz",
        }.get(item.status, item.status)

    d = item.decision
    f = item.features
    if f is None:
        return "özellik yok"
    if f.amount_try is None:
        flags = ", ".join(f.flags) or "tutar yok"
        return f"tutar çıkmadı ({flags}) → impact yalnız konu önseli"
    if f.primary_ratio is None:
        return "payda yok (bilanço eksik) → oran hesaplanamadı"
    parts = [f"oran %{f.primary_ratio:.1f}"]
    if "implausible_ratio" in f.flags:
        parts.append("oran şüpheli ×0.25")
    if d.action == "silent":
        parts.append(f"skor {d.push_score:.2f} < özet eşiği")
    return " · ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Örnek vakalar")
    parser.add_argument("--only-actions", action="store_true", help="yalnız push/özet olanlar")
    args = parser.parse_args()

    cfg = ScoringConfig.load(ROOT / "config" / "scoring.toml")
    resolver = TickerResolver.from_pairs(
        [(ticker, f"{ticker} A.Ş.") for ticker in BALANCE_SHEETS]
    )
    stubs = build_stubs()
    results = asyncio.run(
        process_batch(
            stubs,
            resolver=resolver,
            fundamentals=BALANCE_SHEETS,
            cfg=cfg,
            body_provider=make_body_provider(),
        )
    )

    print(SEP)
    print("ÖRNEK VAKALAR — 12 senaryo, gövdeden tutar (MTRKS cirosu varsayım)")
    print(SEP)
    print(f"  eşikler: push ≥ {cfg.push_threshold} · özet ≥ {cfg.digest_threshold}"
          f" · bütçe {cfg.max_push_per_ticker_per_day}/hisse/gün\n")

    header = (f"  {'#':>2s} {'hisse':6s} {'konu':34s} {'tutar':>13s} {'oran':>7s} "
              f"{'impact':>6s} {'emniyet':>7s} {'skor':>5s}  karar")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, (case, item) in enumerate(zip(CASES, results), 1):
        amount = item.features.amount_try if item.features else None
        amount_txt = f"{amount / 1_000_000:,.0f} mn" if amount is not None else "—"
        ratio = item.features.primary_ratio if item.features else None
        ratio_txt = f"%{ratio:.1f}" if ratio is not None else "—"
        impact = f"{item.decision.impact:.1f}" if item.decision else "—"
        conf = f"{item.decision.confidence:.2f}" if item.decision else "—"
        score = f"{item.decision.push_score:.2f}" if item.decision else "—"
        print(f"  {i:2d} {case.ticker:6s} {case.subject[:34]:34s} {amount_txt:>13s} "
              f"{ratio_txt:>7s} {impact:>6s} {conf:>7s} {score:>5s}  {decision_turkish(item)}")

    print("\n  HER VAKANIN GEREKÇESİ")
    for i, (case, item) in enumerate(zip(CASES, results), 1):
        print(f"\n  {i}. {case.ticker} · {case.subject}")
        print(f"     durum  : {status_turkish(item)} · {decision_turkish(item)}")
        print(f"     gövde  : {case.body[:70]}")
        print(f"     sebep  : {reason_of(item)}")
        print(f"     konu   : {case.note}")

    ranked = rank(results, budget_per_ticker=cfg.max_push_per_ticker_per_day)
    notifier = FakeNotifier()
    report = asyncio.run(dispatch(results, notifier, day=DAY, cfg=cfg))

    print("\n" + SEP)
    print("SAHTE ALICIDA GÖRÜLEN MESAJLAR")
    print(SEP)
    print(f"  {report.summary()}\n")
    for key, message in notifier.sent:
        if args.only_actions and key.startswith("digest:"):
            continue
        print(f"[{key}]")
        print(message)
        print()

    if not args.only_actions and report.digest_text:
        print("  (özet yukarıda; sıra: önce push'lar, en sonda özet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
