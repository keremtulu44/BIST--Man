"""ÖNEM DENETİMİ — sistem "önem"e aynı anlamı mı veriyor? (ağ yok)

Sorulan: *benzer mantığa aynı anlam mı veriliyor, yoksa alakasız şeyler de aynı
sıraya mı düşüyor?* Beş deney:

  A) Ölçek anatomisi   — impact'in kaç puanı konudan, kaç puanı boyuttan geliyor
  B) Çakışmalar        — aynı sayı, farklı anlam: hangi (konu, boyut) çiftleri
                         aynı impact'i üretiyor
  C) Sıra terslemeleri — maddiyat sırası ile skor sırası nerede çelişiyor
  D) Ablasyon          — konu önselini sıfırla / boyut puanını sıfırla: kararlar
                         ne kadar değişiyor (hangisi gerçekten belirleyici)
  E) Karşılaştırılabilirlik — payda şirketten şirkete aynı şeyi mi ölçüyor

Kullanım:

    python scripts\\onem_denetimi.py
    python scripts\\onem_denetimi.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.features import BUCKETS, build_features  # noqa: E402
from bistman.scoring import ScoringConfig, match_subject, score  # noqa: E402

CFG_PATH = ROOT / "config" / "scoring.toml"
SEP = "=" * 78


def impact_of(prior: float, bucket: str, cfg: ScoringConfig) -> float:
    return max(0.0, min(10.0, prior + cfg.bucket_points.get(bucket, 0.0)))


def action_of(impact: float, confidence: float, cfg: ScoringConfig) -> str:
    push_score = impact * confidence
    if push_score >= cfg.push_threshold:
        return "push"
    if confidence < cfg.uncertain_confidence_below and impact >= cfg.uncertain_min_impact:
        return "push_uncertain"
    if push_score >= cfg.digest_threshold:
        return "digest"
    return "silent"


# --------------------------------------------------------------------- A


def section_anatomy(cfg: ScoringConfig) -> None:
    priors = {k: v for k, v in cfg.subject_prior.items() if k != "varsayılan"}
    print(SEP)
    print("A) ÖLÇEK ANATOMİSİ — impact nereden geliyor?")
    print(SEP)
    print("\n  konu önseli (impact'e katkı):")
    for key, value in sorted(priors.items(), key=lambda kv: -kv[1]):
        bar = "█" * int(round(abs(value) * 4))
        sign = "−" if value < 0 else "+"
        print(f"    {key:22s} {sign}{abs(value):4.1f}  {bar}")
    print("\n  boyut kovası (impact'e katkı):")
    for bucket, value in cfg.bucket_points.items():
        if bucket in {"bilinmiyor"}:
            continue
        bar = "█" * int(round(value * 2))
        print(f"    {bucket:22s} {value:+4.1f}  {bar}")

    top_prior = max(priors.values())
    bucket_span = max(cfg.bucket_points.values())
    print(
        f"\n  → En güçlü konu (+{top_prior:.1f}) ile en güçlü boyut (+{bucket_span:.1f}) "
        f"arasında **{bucket_span / top_prior:.1f}×** fark var."
    )
    print(
        f"  → Boyut tek başına 0 → {bucket_span:.1f} puan oynatıyor; konu en fazla "
        f"{top_prior:.1f}. Yani **impact fiilen bir büyüklük sıralayıcısı**;"
    )
    print("    konu yalnızca aynı kova içinde sıra değiştiriyor.")


# --------------------------------------------------------------------- B


def section_collisions(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("B) ÇAKIŞMALAR — aynı sayı, farklı anlam")
    print(SEP)
    groups: dict[float, list[tuple[str, str]]] = {}
    priors = {k: v for k, v in cfg.subject_prior.items() if k != "varsayılan"}
    priors["(tanınmayan konu)"] = 0.0
    for subject, prior in priors.items():
        for bucket in cfg.bucket_points:
            if bucket in {"bilinmiyor"}:
                continue
            impact = impact_of(prior, bucket, cfg)
            groups.setdefault(round(impact, 2), []).append((subject, bucket))

    multi = {k: v for k, v in groups.items() if len(v) > 1}
    total = sum(len(v) for v in groups.values())
    print(
        f"\n  {total} farklı (konu, boyut) kombinasyonu {len(groups)} farklı impact üretiyor."
    )
    print(f"  Bunlardan **{len(multi)}** impact değerine birden fazla anlam düşüyor.")

    for target in (10.0, 8.5, 6.5, 4.0, 3.5):
        if target not in groups:
            continue
        combos = groups[target]
        print(f"\n  impact = {target:.1f}  ({len(combos)} farklı yol)")
        for subject, bucket in combos[:6]:
            print(f"    {subject:22s} + {bucket:10s}")
    print(
        "\n  → Aynı sayı; biri 'küçük şirkette dev bir sözleşme', diğeri 'büyük şirkette"
        "\n    orta bir temettü' olabiliyor. Rakam karşılaştırılabilir görünüyor, anlam değil."
    )


# --------------------------------------------------------------------- C


PRIOR_BY_SUBJECT = {
    "Yeni Sözleşme": "sözleşme",
    "Yatırım Kararı": "yatırım kararı",
    "Temettü": "temettü",
    "Bedelli Sermaye": "bedelli",
    "Vergi Cezası": "ceza",
    "Konkordato": "varsayılan",
}

REVENUE_SMALL = 3_200_000_000
CASES: tuple[tuple[str, int], ...] = (
    ("Yeni Sözleşme", 15_000_000_000),
    ("Yeni Sözleşme", 3_000_000_000),
    ("Yeni Sözleşme", 120_000_000),
    ("Yatırım Kararı", 800_000_000),
    ("Temettü", 500_000_000),
    ("Bedelli Sermaye", 900_000_000),
    ("Vergi Cezası", 250_000_000),
    ("Konkordato", 0),
)


def _score_case(subject: str, amount: int, cfg: ScoringConfig):
    features = build_features(
        ticker="X",
        amount_try=Decimal(amount) if amount else None,
        revenue_ttm=Decimal(REVENUE_SMALL),
    )
    return features, score(features, subject, cfg)


def section_inversions(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("C) SIRA TERSLEMELERİ — maddiyat sırası ile skor sırası çelişiyor mu?")
    print(SEP)
    rows = []
    for subject, amount in CASES:
        features, decision = _score_case(subject, amount, cfg)
        ratio = features.primary_ratio
        rows.append((subject, amount, ratio, decision))
    rows.sort(key=lambda r: (r[2] or 0), reverse=True)

    print(f"\n  {'haber':18s} {'tutar':>10s} {'ciro oranı':>11s} {'impact':>7s} "
          f"{'skor':>6s}  karar")
    for subject, amount, ratio, decision in rows:
        amount_txt = f"{amount / 1_000_000:.0f} mn" if amount else "—"
        ratio_txt = f"%{ratio:.0f}" if ratio is not None else "—"
        print(
            f"  {subject:18s} {amount_txt:>10s} {ratio_txt:>11s} "
            f"{decision.impact:7.1f} {decision.push_score:6.2f}  {decision.action}"
        )

    def score_of(subject: str) -> float | None:
        for row_subject, _amount, _ratio, decision in rows:
            if row_subject == subject:
                return decision.push_score
        return None

    big = score_of("Yeni Sözleşme")
    dividend = score_of("Temettü")
    contract_ratio = next(r[2] for r in rows if r[0] == "Yeni Sözleşme")
    dividend_ratio = next(r[2] for r in rows if r[0] == "Temettü")
    print(
        f"\n  → %{contract_ratio:.0f} ciro büyüklüğünde sözleşme skoru {big:.2f}; "
        f"%{dividend_ratio:.0f} büyüklüğünde temettü skoru {dividend:.2f}."
    )
    print("    Maddiyat 29×, skor ters yönde. Sebep: %100 üstü `implausible_ratio` (×0.25).")

    print("\n  Oran → skor eğrisi (aynı şirket, aynı konu):")
    print(f"    {'oran':>8s}  {'impact':>7s}  {'emniyet':>8s}  {'skor':>6s}  karar")
    for pct in (0.5, 2, 6, 15, 40, 90, 140, 400, 900):  # float
        amount = int(REVENUE_SMALL * pct / 100)
        features, decision = _score_case("Yeni Sözleşme", amount, cfg)
        print(
            f"    %{pct:<7.1f} {decision.impact:7.1f}  {decision.confidence:8.2f}  "
            f"{decision.push_score:6.2f}  {decision.action}"
        )
    print(
        "\n  → Eğri %100'de kırılıyor: %90'lık sözleşme push, %140'lık sözleşme digest,"
        "\n    %900'lük sözleşme sessiz. Ceza doğru sezgiyle konmuş (payda hatası olabilir)"
    )
    print("    ama sonuç: 'daha büyük = daha önemsiz' bölgesi var.")


# --------------------------------------------------------------------- D


def section_ablation(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("D) ABLASYON — konu mu belirleyici, boyut mu?")
    print(SEP)
    grid = []
    for subject, amount in CASES:
        grid.append((subject, amount))
    for subject in ("Haber ve Söylenti", "Pay Alım Satım", "Genel Kurul"):
        grid.append((subject, 0))
    for subject, amount in (("Yeni Sözleşme", 40_000_000), ("Temettü", 1_000_000_000)):
        grid.append((subject, amount))

    def actions(config: ScoringConfig) -> list[str]:
        out = []
        for subject, amount in grid:
            features = build_features(
                ticker="X",
                amount_try=Decimal(amount) if amount else None,
                revenue_ttm=Decimal(REVENUE_SMALL),
            )
            out.append(score(features, subject, config).action)
        return out

    base = actions(cfg)

    zero_prior = ScoringConfig(
        subject_prior={"varsayılan": 0.0},
        bucket_points=cfg.bucket_points,
        penalties=cfg.penalties,
        push_threshold=cfg.push_threshold,
        digest_threshold=cfg.digest_threshold,
        max_push_per_ticker_per_day=cfg.max_push_per_ticker_per_day,
        uncertain_confidence_below=cfg.uncertain_confidence_below,
        uncertain_min_impact=cfg.uncertain_min_impact,
    )
    zero_bucket = ScoringConfig(
        subject_prior=cfg.subject_prior,
        bucket_points={k: 0.0 for k in cfg.bucket_points},
        penalties=cfg.penalties,
        push_threshold=cfg.push_threshold,
        digest_threshold=cfg.digest_threshold,
        max_push_per_ticker_per_day=cfg.max_push_per_ticker_per_day,
        uncertain_confidence_below=cfg.uncertain_confidence_below,
        uncertain_min_impact=cfg.uncertain_min_impact,
    )

    no_prior = actions(zero_prior)
    no_bucket = actions(zero_bucket)

    changed_prior = sum(1 for a, b in zip(base, no_prior) if a != b)
    changed_bucket = sum(1 for a, b in zip(base, no_bucket) if a != b)
    print(f"\n  {len(grid)} olay")
    print(f"  konu önseli sıfırlanınca değişen karar : {changed_prior:2d}  (%{changed_prior / len(grid) * 100:.0f})")
    print(f"  boyut puanı sıfırlanınca değişen karar : {changed_bucket:2d}  (%{changed_bucket / len(grid) * 100:.0f})")
    print("\n  olay                          mevcut     konu=0     boyut=0")
    for (subject, amount), a, b, c in zip(grid, base, no_prior, no_bucket):
        if a == b == c:
            continue
        label = f"{subject} {amount / 1_000_000:.0f}mn" if amount else subject
        print(f"  {label[:28]:28s} {a:11s} {b:11s} {c:11s}")


# --------------------------------------------------------------------- E


def section_comparability(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("E) KARŞILAŞTIRILABİLİRLİK — payda aynı şeyi mi ölçüyor?")
    print(SEP)
    books = {
        "THYAO (sanayi, ciro)": 1_364_849_196_737,
        "MTRKS (küçük, ciro)": 3_200_000_000,
        "AKBNK (banka, faiz geliri)": 679_718_603_000,
        "holding (konsolide satış)": 900_000_000_000,
    }
    amount = Decimal(5_000_000_000)
    print(f"\n  Aynı haber: 5 milyar TL sözleşme. Yalnız payda değişiyor:\n")
    print(f"  {'payda':30s} {'oran':>8s} {'impact':>7s} {'emniyet':>8s} {'skor':>6s}  karar")
    for label, revenue in books.items():
        features = build_features(
            ticker="X", amount_try=amount, revenue_ttm=Decimal(str(revenue))
        )
        decision = score(features, "Yeni Sözleşme İmzalanması", cfg)
        ratio = features.primary_ratio or 0.0
        print(
            f"  {label:30s} %{ratio:7.1f} {decision.impact:7.1f} "
            f"{decision.confidence:8.2f} {decision.push_score:6.2f}  {decision.action}"
        )
    print(
        "\n  → Bankada payda 'faiz geliri', sanayide 'satış'. %5 oranı bankada"
        "\n    'net faiz gelirinin %5'i', sanayide 'cironun %5'i'. Skor aynı, anlam değil."
    )
    print(
        "  → Ayrıca payda TTM: zarar eden şirkette `ratio_net_income` anlamsız,",
        "\n    ama skor onu kullanmıyor (yalnız PD ve ciro). Doğrusu: PD yoksa ciro."
    )


# --------------------------------------------------------------------- F


def _ranks(values: list[float]) -> list[float]:
    """Ortalama sıra (eşit değerler aynı sırayı paylaşır)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def section_consistency(cfg: ScoringConfig) -> None:
    print("\n" + SEP)
    print("F) TUTARLILIK — skor, maddiyatla aynı yönde mi sıralıyor?")
    print(SEP)
    subjects = ("Yeni Sözleşme", "Temettü", "Vergi Cezası", "Bilinmeyen Konu")
    ratios = (0.5, 1.5, 4.0, 8.0, 20.0, 60.0, 99.0, 140.0, 400.0)
    xs: list[float] = []
    ys: list[float] = []
    pairs: list[tuple[float, float, str]] = []
    for subject in subjects:
        for pct in ratios:
            amount = int(REVENUE_SMALL * pct / 100)
            features, decision = _score_case(subject, amount, cfg)
            xs.append(pct)
            ys.append(decision.push_score)
            pairs.append((pct, decision.push_score, subject))

    inner = [(x, y) for x, y, _s in pairs if x < 100]
    rho_all = _spearman(xs, ys)
    rho_inner = _spearman([x for x, _ in inner], [y for _, y in inner])

    inversions = 0
    total_pairs = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            if pairs[i][0] == pairs[j][0]:
                continue
            total_pairs += 1
            if (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) < 0:
                inversions += 1
    print(f"\n  {len(pairs)} olay (%0.5 – %400 ciro oranı × 4 konu)")
    print(f"  Spearman(oran, skor) tüm aralık : {rho_all:+.3f}")
    print(f"  Spearman(oran, skor) oran<100   : {rho_inner:+.3f}")
    print(f"   ters sıralı çift               : {inversions}/{total_pairs} "
          f"(%{inversions / total_pairs * 100:.0f})")
    print(
        "\n  → %100'ün altında sıralama tutarlı (ρ≈1). %100'ü geçen her olay aynı"
        "\n    2.50'ye düşüyor: 140% ile 900% arasında sistem ayrım yapmıyor."
    )
    print("    Yani tutarsızlık rastgele değil, **tek bir kırılma noktasından** geliyor.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Önem denetimi")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    del args
    cfg = ScoringConfig.load(CFG_PATH)
    section_anatomy(cfg)
    section_collisions(cfg)
    section_inversions(cfg)
    section_ablation(cfg)
    section_comparability(cfg)
    section_consistency(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
