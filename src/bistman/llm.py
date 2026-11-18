"""LLM'in hattaki YERİ — dar, doğrulanabilir, geri çekilebilir.

İlke: **LLM önerir, kural karar verir.** LLM'in çıktısı hiçbir zaman skora
doğrudan yazılmaz; yalnız hattın bugün **eksik olan girdilerini** doldurur:

    eksik olan            LLM ne yapar                    kod ne yapar
    ─────────────────     ──────────────────────────────  ─────────────────────
    konu (F1)             başlık+gövdeden sınıf önerir     önseli tablodan uygular
    yön (F9)              olumlu/olumsuz/nötr önerir       mesajda etiketler
    tutarsız ama maddi    kritik/önemli/rutin önerir       bildirimi zorunlu kılar
    (F2)

LLM'in **yapmadığı** işler (pazarlık konusu değil):

  * aritmetik: oran, TTM, skor — hepsi kodda (yoksa sessiz hata üretir),
  * eşik kararı: push/özet/sessiz — politika tablosu,
  * sayı çıkarma: tutar `parsing/money.py`'de; LLM yalnız "hangi ifade ana
    tutar" işaret eder, sayıyı **kendisi hesaplamaz** (bkz. `AmountHint`).

Ağ yok: sağlayıcı `complete` fonksiyonu olarak enjekte edilir (OpenAI, Anthropic,
Gemini, yerel model — hepsi aynı arayüz). Anahtar yoksa `RuleSubjectReader`
çalışır; hat aynı kalır.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol

from .parsing.text import tr_lower
from .scoring import ScoringConfig

Severity = Literal["kritik", "onemli", "rutin"]
Direction = Literal["olumlu", "olumsuz", "notr"]

SEVERITIES: tuple[str, ...] = ("kritik", "onemli", "rutin")
DIRECTIONS: tuple[str, ...] = ("olumlu", "olumsuz", "notr")

# Şema: LLM yalnız bu alanları döndürebilir. `score`, `ratio`, `impact` gibi
# sayısal alanlar **kasten yok** — model sayı uyduramaz.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"subject_key", "severity", "direction", "amount_hint", "confidence", "rationale"}
)


@dataclass(frozen=True, slots=True)
class SubjectRead:
    """LLM'in (ya da kural okuyucunun) önerisi. Tavsiye niteliğinde."""

    subject_key: str
    severity: Severity
    direction: Direction
    confidence: float
    amount_hint: str | None = None
    """Metindeki ana tutar **ifadesi** (ör. "15 milyar TL"). Sayı değil, alıntı."""
    rationale: str = ""
    source: str = "rules"

    @property
    def reliable(self) -> bool:
        return self.confidence >= 0.5


class SubjectReader(Protocol):
    async def read(
        self, *, subject: str, summary: str = "", body: str = ""
    ) -> SubjectRead | None: ...


# --------------------------------------------------------------- kural yolu


_DIRECTION_HINTS: dict[str, tuple[str, ...]] = {
    "olumsuz": (
        "ceza", "dava", "konkordato", "iflas", "işlem yasağı", "islem yasagi",
        "sermaye azaltım", "sermaye azaltim", "üretime ara", "uretime ara",
        "yangın", "yangin", "iptal", "fesih", "kayıp", "zarar",
    ),
    "olumlu": (
        "sözleşme", "sozlesme", "ihale", "sipariş", "siparis", "temettü",
        "temettu", "yatırım kararı", "yatirim karari", "kapasite artış",
        "kapasite artis", "birleşme", "birlesme", "satın alma", "satin alma",
    ),
}

_CRITICAL_HINTS: tuple[str, ...] = (
    "konkordato", "iflas", "işlem yasağı", "islem yasagi", "üretime ara",
    "uretime ara", "fesih", "sermaye azaltım", "sermaye azaltim", "yangın",
    "yangin", "devre kesici",
)

_RULE_PRIORS: frozenset[str] = frozenset(
    {
        "sözleşme", "ihale", "yatırım kararı", "kapasite", "satın alma",
        "birleşme", "haber ve söylenti", "pay alım satım", "temettü",
        "kâr dağıtım", "bedelli", "bedelsiz", "dava", "ceza", "devre kesici",
        "genel kurul",
    }
)


def _contains(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def guess_direction(text: str) -> Direction:
    """Sözlük tabanlı yön tahmini (bugünkü hâl: yön alanı yok, en yakını bu)."""
    lowered = tr_lower(text)
    negative = _contains(lowered, _DIRECTION_HINTS["olumsuz"])
    positive = _contains(lowered, _DIRECTION_HINTS["olumlu"])
    if negative and not positive:
        return "olumsuz"
    if positive and not negative:
        return "olumlu"
    return "notr"


def guess_severity(text: str, prior: float) -> Severity:
    lowered = tr_lower(text)
    if _contains(lowered, _CRITICAL_HINTS):
        return "kritik"
    if prior >= 2.0:
        return "onemli"
    return "rutin"


@dataclass(frozen=True, slots=True)
class RuleSubjectReader:
    """Bugünkü davranış, `SubjectReader` arayüzünde. LLM yokken taban çizgisi."""

    cfg: ScoringConfig

    async def read(
        self, *, subject: str, summary: str = "", body: str = ""
    ) -> SubjectRead | None:
        text = f"{subject} {summary} {body}"
        lowered = tr_lower(subject)
        key, prior = "varsayılan", 0.0
        best = 0
        for candidate in _RULE_PRIORS:
            needle = tr_lower(candidate)
            if needle in lowered and len(needle) > best:
                key, best = candidate, len(needle)
        if key != "varsayılan":
            prior = self.cfg.subject_prior.get(key, 0.0)
        return SubjectRead(
            subject_key=key,
            severity=guess_severity(text, prior),
            direction=guess_direction(text),
            confidence=0.3,  # sözlük tabanlı: düşük güven, bilinçli
            amount_hint=None,
            rationale="sözlük eşleşmesi",
            source="rules",
        )


# ------------------------------------------------------------- LLM yolu


def build_prompt(*, subject: str, summary: str = "", body: str = "") -> str:
    """Sağlayıcı-bağımsız istem. Yanıt **yalnız JSON** olmalı."""
    body_excerpt = body.strip()[:2000]
    return (
        "Aşağıdaki KAP (Kamuyu Aydınlatma Platformu) bildirimini sınıflandır.\n"
        "Yalnız şu JSON'u döndür, başka metin yazma:\n"
        '{"subject_key": "<kısa Türkçe konu, ör. sözleşme>", '
        '"severity": "kritik|onemli|rutin", '
        '"direction": "olumlu|olumsuz|notr", '
        '"amount_hint": "<metindeki ANA tutar ifadesi, aynen; yoksa null>", '
        '"confidence": <0.0-1.0>, "rationale": "<en fazla 20 kelime>"}\n'
        "Kurallar: sayı hesaplama, oran verme, tavsiye verme. Tutarı kendin "
        "hesaplamayacaksın; sadece metindeki ifadeyi kopyalayacaksın.\n\n"
        f"KONU: {subject}\n"
        f"ÖZET: {summary[:800] or '-'}\n"
        f"GÖVDE: {body_excerpt or '-'}\n"
    )


def parse_subject_read(payload: str, *, model: str = "llm") -> SubjectRead | None:
    """LLM yanıtını **katı** şemaya göre doğrular. Bozuksa `None` (fail-open).

    Kasıtlı katılık: model ne kadar ikna edici yazarsa yazsın, şemada olmayan
    alan (`score`, `ratio`, `impact` …) gelirse **tüm yanıt** reddedilir. Böylece
    "model skor uydurdu" sınıfı hata yapısal olarak imkânsız olur.
    """
    raw = (payload or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if set(data) - ALLOWED_FIELDS:
        return None  # şema dışı alan: sayı uydurma girişimi olabilir

    subject_key = data.get("subject_key")
    severity = data.get("severity")
    direction = data.get("direction")
    confidence = data.get("confidence")
    amount_hint = data.get("amount_hint")
    rationale = data.get("rationale") or ""

    if not isinstance(subject_key, str) or not subject_key.strip():
        return None
    if severity not in SEVERITIES or direction not in DIRECTIONS:
        return None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    if amount_hint is not None and not isinstance(amount_hint, str):
        return None
    if not isinstance(rationale, str):
        return None

    return SubjectRead(
        subject_key=subject_key.strip()[:60],
        severity=severity,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, float(confidence))),
        amount_hint=amount_hint.strip() if amount_hint else None,
        rationale=rationale.strip()[:200],
        source=model,
    )


@dataclass(frozen=True, slots=True)
class LlmSubjectReader:
    """Herhangi bir LLM API'si ile çalışır: `complete(prompt) -> yanıt metni`.

    Örnek (ağ çağrısı **enjekte** edilir, bu modül ağ bilmez):

        async def openai_complete(prompt: str) -> str:
            ...  # httpx ile /v1/chat/completions, temperature=0
        reader = LlmSubjectReader(complete=openai_complete, model="gpt-4.1-mini")

    Hata/timeout/bozuk JSON → `None` döner; çağıran taraf `RuleSubjectReader`
    sonucuna düşer. Yani LLM çökerse hat çalışmaya devam eder.
    """

    complete: Callable[[str], Awaitable[str]]
    model: str = "llm"
    timeout: float = 8.0
    fallback: SubjectReader | None = None
    calls: int = 0

    async def read(
        self, *, subject: str, summary: str = "", body: str = ""
    ) -> SubjectRead | None:
        prompt = build_prompt(subject=subject, summary=summary, body=body)
        try:
            payload = await asyncio.wait_for(self.complete(prompt), timeout=self.timeout)
        except (asyncio.TimeoutError, Exception):  # noqa: B014 - sağlayıcı hatası
            payload = ""
        parsed = parse_subject_read(payload, model=self.model) if payload else None
        if parsed is None and self.fallback is not None:
            return await self.fallback.read(subject=subject, summary=summary, body=body)
        return parsed


def apply_policy(read: SubjectRead | None, cfg: ScoringConfig) -> dict[str, object]:
    """Öneriyi **karara değil** işaretlemeye çevirir (bugün için).

    `kritik` konu tutarsız olsa da bildirilir (F2'nin çözümü); `yön` mesajda
    etiket olur (F9). Skor formülüne dokunulmaz — o kalibrasyonun işi.
    """
    if read is None:
        return {"force_notify": False, "dir_label": "", "severity_label": ""}
    return {
        "force_notify": read.severity == "kritik",
        "dir_label": {"olumlu": "▲ olumlu", "olumsuz": "▼ risk", "notr": ""}[read.direction],
        "severity_label": {"kritik": "kritik", "onemli": "", "rutin": ""}[read.severity],
    }
