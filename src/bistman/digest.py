"""Sıralama ve günlük özet — "hangi sırayla, ne gönderilir".

Neden ayrı modül: `process_batch` bir bildirim grubunu işler ve sonucu **geliş
sırasıyla** döndürür (KAP'ın yayın sırası). Kullanıcıya giden sıra bu olmamalı:
önce en yüksek skor, sonra kalanı tek özet mesajında. Ayrıca günlük push
bütçesi (`max_push_per_ticker_per_day`) **burada** uygulanır — `scoring.toml`'da
duruyor ama bugüne kadar hiçbir kod onu uygulamıyordu.

Kurallar (hepsi testli):

1. **Tekilleştirme**: aynı `(bildirim index, hisse)` için önce gönderim kanalı,
   sonra o kanalın sıralama anahtarı belirleyicidir; tekrar sayısı raporda görünür (sessiz eleme yok).
2. **Sıralama**: `push_score` azalan; eşitlikte `impact`, sonra hisse kodu,
   yayın zamanı, index. Deterministik — aynı girdi aynı sırayı verir.
   **İstisna:** `push_uncertain` impact ile sıralanır (emniyeti düşük olduğu
   için skoru düşük; skorla sıralamak büyük olayı temettünün altına gömer).
3. **Bütçe**: hisse başına günde en fazla `budget` push. Aşan push **düşürülmez**,
   özete iner (`[bütçe]` etiketiyle). Kaybetmek yok, sırası değişir.
4. **Özet (digest)**: `digest` kararlı olaylar + bütçeden düşenler, yine skora
   göre sıralı, `max_items` ile sınırlı; kalanı tek satırda sayılır.
5. **Boş özet gönderilmez** (`None`) — "sessizlik = sinyal yok" değil, sadece
   gönderilecek bir şey yok; sayaçlar raporda.

Telegram'a **düz metin** gider: MarkdownV2 kaçırma tuzağı (A.Ş., %3,0) yok.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Iterable, Sequence

from .notify import Notifier, send_checked
from .parsing.dates import to_trt
from .pipeline import ProcessedDisclosure, render_message
from .scoring import ScoringConfig

BUDGET_NOTE = "bütçe: günün {n}. push'u; özete alındı"


@dataclass(frozen=True, slots=True)
class Ranked:
    """Sıralanmış gönderim planı."""

    pushes: tuple[ProcessedDisclosure, ...]
    digest: tuple[ProcessedDisclosure, ...]
    demoted: tuple[ProcessedDisclosure, ...]
    """Bütçe nedeniyle push'tan özete inenler (digest'in içinde de görünürler)."""
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.pushes) + len(self.digest)


def sort_metric(item: ProcessedDisclosure) -> float:
    """Sıralamada kullanılan tek sayı.

    `push_uncertain` (belirsiz ama büyük) **impact** ile sıralanır, push_score ile
    değil. Sebep: o etiket tam da "emniyet düşük olduğu için skor düşük çıkıyor,
    ama olay büyük" demek. `push_score` ile sıralarsak 15 milyarlık sözleşme
    (%469 → skor 2.50, impact 10) 500 milyonluk temettünün (7.50) altına düşer —
    yani etiket kendi amacını boşa çıkarır. Ölçüm: docs/ONEM-DENETIMI.md C.
    """
    assert item.decision is not None
    if item.decision.action == "push_uncertain":
        return item.decision.impact
    return item.decision.push_score


def _sort_key(item: ProcessedDisclosure) -> tuple:
    # Sayısal sıra ayrı, ölçeksiz gündem ayrı: ona yapay skor verme.
    numeric = item.decision is not None and not item.qualitative
    return (
        not numeric,
        -sort_metric(item) if numeric else 0,
        -item.decision.impact if numeric else 0,
        item.ticker or "~",
        item.disclosure.published_at,
        item.disclosure.index,
    )


def _dedupe(results: Sequence[ProcessedDisclosure]) -> tuple[list[ProcessedDisclosure], int]:
    """Aynı (index, hisse) tek kez; atıl bir kopya aktif planı gölgelemez."""
    best: dict[tuple[int, str], ProcessedDisclosure] = {}
    duplicates = 0
    priority = {"push": 2, "push_uncertain": 2, "digest": 1}
    for item in results:
        key = (item.disclosure.index, item.ticker or "")
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        duplicates += 1
        a, b = priority.get(item.planned_action, 0), priority.get(current.planned_action, 0)
        if a > b or (a == b and _sort_key(item) < _sort_key(current)):
            best[key] = item
    return list(best.values()), duplicates


def rank(
    results: Iterable[ProcessedDisclosure], *, budget_per_ticker: int = 3
) -> Ranked:
    """Sonuçları gönderim planına çevirir (saf fonksiyon, yan etki yok)."""
    items = list(results)
    counts: dict[str, int] = {"results": len(items)}
    unique, duplicates = _dedupe(items)
    counts["duplicate"] = duplicates

    pushable: list[ProcessedDisclosure] = []
    digestable: list[ProcessedDisclosure] = []
    for item in unique:
        if item.planned_action in {"push", "push_uncertain"}:
            pushable.append(item)
        elif item.planned_action == "digest":
            digestable.append(item)

    pushable.sort(key=_sort_key)

    used: dict[tuple[date, str], int] = {}
    pushes: list[ProcessedDisclosure] = []
    demoted: list[ProcessedDisclosure] = []
    for item in pushable:
        key = (to_trt(item.disclosure.published_at).date(), item.ticker or "?")
        seen = used.get(key, 0)
        used[key] = seen + 1
        if seen >= budget_per_ticker:
            demoted.append(
                replace(item, notes=item.notes + (BUDGET_NOTE.format(n=seen + 1),))
            )
            continue
        pushes.append(item)

    digest = sorted(demoted + digestable, key=_sort_key)
    counts.update(
        {
            "push": len(pushes),
            "digest": len(digest),
            "demoted": len(demoted),
            "silent_or_filtered": len(items) - len(pushes) - len(digest) - duplicates,
        }
    )
    return Ranked(
        pushes=tuple(pushes),
        digest=tuple(digest),
        demoted=tuple(demoted),
        counts=counts,
    )


# --------------------------------------------------------------------- özet


def _label(item: ProcessedDisclosure) -> str:
    return item.ticker or "Piyasa gündemi"


def _one_line(item: ProcessedDisclosure) -> str:
    if item.qualitative:
        return f"{item.brief} (bildirim #{item.disclosure.index})"
    decision = item.decision
    assert decision is not None
    ratio = item.features.primary_ratio if item.features else None
    parts = [f"skor {decision.push_score:.2f}", f"impact {decision.impact:.1f}"]
    if ratio is not None:
        parts.append(f"oran %{ratio:.1f}")
    if "implausible_ratio" in (item.features.flags if item.features else ()):
        parts.append("oran şüpheli")
    if any(note.startswith("bütçe:") for note in item.notes):
        parts.append("bütçe")
    if item.brief:
        parts.append(item.brief)
    return " · ".join(parts)


def render_digest(
    ranked: Ranked,
    *,
    day: date,
    max_items: int = 12,
    title: str = "GÜNLÜK ÖZET",
    pushed_ok: int | None = None,
) -> str | None:
    """Özet mesajı. Gönderilecek olay yoksa `None` (boş mesaj atılmaz)."""
    if not ranked.digest:
        return None

    planned = len(ranked.pushes)
    push_status = (f"{planned} ayrı push planlandı" if pushed_ok is None
                   else f"{pushed_ok}/{planned} ayrı push onaylandı")
    shown = ranked.digest[:max_items]
    lines = [
        f"{title} · {day.strftime('%d.%m.%Y')}",
        f"{len(ranked.digest)} olay · {push_status}",
        "",
    ]
    for i, item in enumerate(shown, 1):
        ticker = _label(item)
        subject = item.disclosure.subject.strip()
        lines.append(f"{i}. {ticker} · {subject}")
        lines.append(f"   {_one_line(item)}")

    rest = ranked.digest[max_items:]
    if rest:
        listed = ", ".join(
            f"{_label(i)} · {i.disclosure.subject}" if i.qualitative else f"{_label(i)} {i.decision.push_score:.2f}"
            for i in rest[:5]
        )
        more = f" (+{len(rest) - min(len(rest), 5)} daha)" if len(rest) > 5 else ""
        lines.append("")
        lines.append(f"… {len(rest)} olay daha: {listed}{more}")
    return "\n".join(lines)


# ----------------------------------------------------------------- gönderim


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Ne gönderildi, ne başarısız oldu — sayaçlar raporlanır."""

    pushed_ok: int
    pushed_failed: int
    digest_sent: bool
    digest_failed: bool
    ranked: Ranked
    digest_text: str | None = None
    failed_push_keys: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.pushed_failed == 0 and not self.digest_failed

    def summary(self) -> str:
        digest_state = "başarısız" if self.digest_failed else "gönderildi" if self.digest_sent else "yok"
        lines = [
            f"push {self.pushed_ok}/{self.pushed_ok + self.pushed_failed}",
            f"özet {digest_state}",
        ]
        if self.ranked.demoted:
            lines.append(f"bütçe {len(self.ranked.demoted)}")
        if self.ranked.counts.get("duplicate"):
            lines.append(f"tekrar {self.ranked.counts['duplicate']}")
        return " · ".join(lines)


async def dispatch(
    results: Iterable[ProcessedDisclosure],
    notifier: Notifier,
    *,
    day: date,
    cfg: ScoringConfig,
    max_items: int = 12,
) -> DispatchReport:
    """Push'ları önem sırasıyla, özeti tek mesaj olarak gönderir.

    Tek TRT gününün planını bu çağrıda gönderir: önce push, en sonda özet.
    Zamanlayıcı veya çağrılar arası kalıcı bir gönderim günlüğü değildir.
    """
    batch = list(results)
    if any(i.delivery != "not_attempted" for i in batch):
        raise ValueError("doğrudan gönderim denenmiş; dispatch için notifier=None ile planla")
    if any(to_trt(i.disclosure.published_at).date() != day for i in batch):
        raise ValueError("dispatch yalnız belirtilen TRT gününün kayıtlarını kabul eder")
    ranked = rank(batch, budget_per_ticker=cfg.max_push_per_ticker_per_day)
    ok = 0
    failed_keys: list[str] = []
    for item in ranked.pushes:
        key = f"{item.disclosure.index}:{item.ticker or '?'}"
        if await send_checked(notifier, render_message(item), key=key):
            ok += 1
        else:
            failed_keys.append(key)

    text = render_digest(ranked, day=day, max_items=max_items, pushed_ok=ok)
    digest_sent = False
    digest_failed = False
    if text is not None:
        if await send_checked(notifier, text, key=f"digest:{day.isoformat()}"):
            digest_sent = True
        else:
            digest_failed = True

    return DispatchReport(
        pushed_ok=ok,
        pushed_failed=len(failed_keys),
        failed_push_keys=tuple(failed_keys),
        digest_sent=digest_sent,
        digest_failed=digest_failed,
        ranked=ranked,
        digest_text=text,
    )
