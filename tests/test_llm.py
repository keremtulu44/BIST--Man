"""LLM sınırı — ağ yok: sağlayıcı enjekte edilir, şema katıdır."""

from __future__ import annotations

import asyncio
import json

import pytest

from bistman.llm import (
    LlmSubjectReader,
    RuleSubjectReader,
    SubjectRead,
    apply_policy,
    build_prompt,
    guess_direction,
    guess_severity,
    parse_subject_read,
)
from bistman.scoring import ScoringConfig

CFG = ScoringConfig.load()
GOOD = {
    "subject_key": "konkordato",
    "severity": "kritik",
    "direction": "olumsuz",
    "amount_hint": None,
    "confidence": 0.9,
    "rationale": "şirket konkordato talebinde bulundu",
}


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- istem


def test_prompt_includes_text_and_forbids_numbers() -> None:
    prompt = build_prompt(
        subject="Konkordato Talebi",
        summary="Şirket konkordato talep etmiştir.",
        body="x" * 5000,
    )
    assert "Konkordato Talebi" in prompt
    assert "hesaplamayacaksın" in prompt
    assert len(prompt) < 4000, "gövde kırpılmalı (maliyet)"


# --------------------------------------------------------------- şema


def test_parse_valid_payload() -> None:
    read = parse_subject_read(json.dumps(GOOD), model="test")
    assert read is not None
    assert read.severity == "kritik"
    assert read.direction == "olumsuz"
    assert read.source == "test"


def test_parse_rejects_unknown_fields() -> None:
    """Model 'score' uydurursa tüm yanıt reddedilir — sayı sızmasın."""
    payload = dict(GOOD, score=9.5)
    assert parse_subject_read(json.dumps(payload)) is None


def test_parse_rejects_bad_enum() -> None:
    payload = dict(GOOD, severity="cok-onemli")
    assert parse_subject_read(json.dumps(payload)) is None


def test_parse_rejects_non_json() -> None:
    assert parse_subject_read("Bence bu haber çok önemli, skoru 9 olmalı.") is None
    assert parse_subject_read("") is None


def test_parse_rejects_numeric_amount_hint() -> None:
    payload = dict(GOOD, amount_hint=15_000_000_000)
    assert parse_subject_read(json.dumps(payload)) is None


def test_parse_clamps_confidence() -> None:
    payload = dict(GOOD, confidence=4.2)
    read = parse_subject_read(json.dumps(payload))
    assert read is not None and read.confidence == 1.0


def test_parse_strips_markdown_fence() -> None:
    payload = "```json\n" + json.dumps(GOOD) + "\n```"
    read = parse_subject_read(payload)
    assert read is not None and read.subject_key == "konkordato"


# ------------------------------------------------------- sağlayıcı sınırı


def test_llm_reader_uses_injected_complete() -> None:
    seen: list[str] = []

    async def complete(prompt: str) -> str:
        seen.append(prompt)
        return json.dumps(GOOD)

    reader = LlmSubjectReader(complete=complete, model="fake-model")
    read = run(reader.read(subject="Konkordato Talebi", body="..."))
    assert read is not None and read.subject_key == "konkordato"
    assert "Konkordato Talebi" in seen[0]


def test_llm_failure_falls_back_to_rules() -> None:
    async def broken(prompt: str) -> str:
        raise RuntimeError("API 500")

    reader = LlmSubjectReader(
        complete=broken, model="fake", fallback=RuleSubjectReader(CFG)
    )
    read = run(reader.read(subject="Konkordato Talebi"))
    assert read is not None
    assert read.source == "rules"
    assert read.severity == "kritik"


def test_llm_garbage_falls_back_to_rules() -> None:
    async def garbage(prompt: str) -> str:
        return "kesinlikle alınmalı!!"

    reader = LlmSubjectReader(
        complete=garbage, model="fake", fallback=RuleSubjectReader(CFG)
    )
    read = run(reader.read(subject="Vergi Cezası"))
    assert read is not None and read.source == "rules"


def test_llm_timeout_does_not_hang() -> None:
    import asyncio as aio

    async def slow(prompt: str) -> str:
        await aio.sleep(5)
        return json.dumps(GOOD)

    reader = LlmSubjectReader(complete=slow, model="fake", timeout=0.01)
    assert run(reader.read(subject="x")) is None


# --------------------------------------------------------- kural okuyucu


def test_rule_reader_matches_current_priors() -> None:
    reader = RuleSubjectReader(CFG)
    read = run(reader.read(subject="Yeni Sözleşme İmzalanması"))
    assert read is not None
    assert read.subject_key == "sözleşme"
    assert read.direction == "olumlu"
    assert read.severity == "onemli"  # önsel 2.5


def test_rule_reader_marks_bad_news_critical() -> None:
    reader = RuleSubjectReader(CFG)
    read = run(reader.read(subject="Konkordato Talebi"))
    assert read is not None
    assert read.severity == "kritik"
    assert read.direction == "olumsuz"


def test_rule_reader_confidence_is_low_by_design() -> None:
    """Sözlük tabanlı okuyucu kendine güvenmemeli; LLM'in katkısı ölçülebilsin."""
    read = run(RuleSubjectReader(CFG).read(subject="Bedelli Sermaye Artırımı"))
    assert read is not None and read.confidence < 0.5
    assert not read.reliable


def test_guess_direction_and_severity() -> None:
    assert guess_direction("Şirketimize vergi cezası kesilmiştir") == "olumsuz"
    assert guess_direction("Yeni sözleşme imzalandı") == "olumlu"
    assert guess_direction("Genel kurul toplandı") == "notr"
    assert guess_severity("Konkordato talebi", 0.0) == "kritik"
    assert guess_severity("Sözleşme", 2.5) == "onemli"
    assert guess_severity("Genel kurul", 0.0) == "rutin"


# ------------------------------------------------------------- politika


def test_kritik_forces_notification_even_without_amount() -> None:
    """F2'nin çözümü: tutarsız ama kritik haber bildirilir."""
    read = SubjectRead(
        subject_key="konkordato", severity="kritik", direction="olumsuz", confidence=0.9
    )
    policy = apply_policy(read, CFG)
    assert policy["force_notify"] is True
    assert policy["dir_label"] == "▼ risk"


def test_policy_without_read_is_neutral() -> None:
    assert apply_policy(None, CFG) == {
        "force_notify": False,
        "dir_label": "",
        "severity_label": "",
    }
