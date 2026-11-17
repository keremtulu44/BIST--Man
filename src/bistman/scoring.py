"""Kural tabanlı skor motoru — v1'de LLM'in yerini tutar.

Neden kurallar: LLM hatası sessizdir (yanlış ama ikna edici), kural hatası
gürültülüdür (test edilebilir). Önce kural motorunu ölçüp LLM'in katkısını
sonra görmek istiyoruz (PLAN-V1 Faz 5 → 7).

Formül:
    impact     = clamp(konu_prior + oran_bucket_puanı, 0, 10)
    confidence = ∏ cezalar  (çarpımsal: iki sorun varsa ceza katlanır)
    push_score = impact × confidence

Karar:
    push_score >= eşik                     -> push
    emniyet düşük & impact yüksek          -> push_uncertain  (saklamıyoruz, etiketliyoruz)
    push_score >= özet eşiği               -> digest
    aksi                                   -> silent

Her karar **kendi gerekçesini** üretir (LLM'siz açıklanabilirlik).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from .features import Features, ratio_bucket
from .parsing.text import tr_lower

Action = Literal["push", "push_uncertain", "digest", "silent"]
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCORING_PATH = REPO_ROOT / "config" / "scoring.toml"


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    subject_prior: dict[str, float]
    bucket_points: dict[str, float]
    penalties: dict[str, float]
    push_threshold: float
    digest_threshold: float
    max_push_per_ticker_per_day: int
    uncertain_confidence_below: float
    uncertain_min_impact: float

    @classmethod
    def load(cls, path: Path | None = None) -> ScoringConfig:
        raw = tomllib.loads((path or DEFAULT_SCORING_PATH).read_text(encoding="utf-8"))
        policy = raw.get("policy", {})
        return cls(
            subject_prior={k: float(v) for k, v in raw.get("subject_prior", {}).items()},
            bucket_points={k: float(v) for k, v in raw.get("ratio_bucket_points", {}).items()},
            penalties={k: float(v) for k, v in raw.get("penalties", {}).items()},
            push_threshold=float(policy.get("push_threshold", 6.5)),
            digest_threshold=float(policy.get("digest_threshold", 3.5)),
            max_push_per_ticker_per_day=int(policy.get("max_push_per_ticker_per_day", 3)),
            uncertain_confidence_below=float(policy.get("uncertain_confidence_below", 0.4)),
            uncertain_min_impact=float(policy.get("uncertain_min_impact", 6.0)),
        )


@dataclass(frozen=True, slots=True)
class Decision:
    impact: float
    confidence: float
    push_score: float
    action: Action
    subject_factor: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def should_notify(self) -> bool:
        return self.action in {"push", "push_uncertain"}


def match_subject(subject: str, prior: dict[str, float]) -> tuple[str, float]:
    """Konu metninde en uzun eşleşen anahtarı bulur; yoksa 'varsayılan'."""
    haystack = tr_lower(subject or "")
    best_key = "varsayılan"
    best_len = 0
    for key, value in prior.items():
        if key == "varsayılan":
            continue
        needle = tr_lower(key)
        if needle and needle in haystack and len(needle) > best_len:
            best_key, best_len = key, len(needle)
    return best_key, prior.get(best_key, prior.get("varsayılan", 0.0))


def score(features: Features, subject: str, cfg: ScoringConfig) -> Decision:
    key, subject_factor = match_subject(subject, cfg.subject_prior)

    bucket = ratio_bucket(features.primary_ratio)
    bucket_points = cfg.bucket_points.get(bucket, 0.0)
    impact = max(0.0, min(10.0, subject_factor + bucket_points))

    confidence = features.confidence(penalties=cfg.penalties)
    push_score = impact * confidence

    reasons: list[str] = [
        f"konu: {key} ({subject_factor:+.1f})",
        (
            f"oran: %{features.primary_ratio:.2f} ({bucket} {bucket_points:+.1f})"
            if features.primary_ratio is not None
            else "oran: hesaplanamadı (+0.0)"
        ),
        f"impact: {impact:.1f}",
        f"emniyet: {confidence:.2f}"
        + (
            " (" + ", ".join(f"{f} ×{cfg.penalties.get(f, 1.0):.2f}" for f in features.flags) + ")"
            if features.flags
            else ""
        ),
        f"push_score: {push_score:.2f}",
    ]

    if push_score >= cfg.push_threshold:
        action: Action = "push"
    elif confidence < cfg.uncertain_confidence_below and impact >= cfg.uncertain_min_impact:
        action = "push_uncertain"
        reasons.append("belirsiz ama büyük: etiketiyle gönderiliyor")
    elif push_score >= cfg.digest_threshold:
        action = "digest"
    else:
        action = "silent"

    return Decision(
        impact=impact,
        confidence=confidence,
        push_score=push_score,
        action=action,
        subject_factor=subject_factor,
        reasons=tuple(reasons),
    )


def score_many(
    items: Iterable[tuple[Features, str]], cfg: ScoringConfig
) -> list[Decision]:
    return [score(features, subject, cfg) for features, subject in items]
