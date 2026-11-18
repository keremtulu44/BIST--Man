"""Sıralama ve günlük özet — "hangi sırayla, ne gönderilir".

Neden ayrı modül: `process_batch` bir bildirim grubunu işler ve sonucu **geliş
sırasıyla** döndürür (KAP'ın yayın sırası). Kullanıcıya giden sıra bu olmamalı:
önce en yüksek skor, sonra kalanı tek özet mesajında. Ayrıca günlük push
bütçesi (`max_push_per_ticker_per_day`) **burada** uygulanır — `scoring.toml`'da
duruyor ama bugüne kadar hiçbir kod onu uygulamıyordu.

Kurallar (hepsi testli):

1. **Tekilleştirme**: aynı `(bildirim index, hisse)` iki kez gelirse en yüksek
   skorlu olan kalır; tekrar sayısı raporda görünür (sessiz eleme yok).
2. **Sıralama**: `push_score` azalan; eşitlikte `impact`, sonra hisse kodu,
   yayın zamanı, index. Deterministik — aynı girdi aynı sırayı verir.
   **İstisna:** `push_uncertain` impact ile sıralanır (emniyeti düşük olduğu
   için skoru düşük; skorla sıralamak büyük olayı temettünün altına gömer).
3. **Bütçe**: hisse başına günde en fazla `budget` push. Aşan push **düşürülmez**,
   özetin en üstüne iner (`[bütçe]` etiketiyle). Kaybetmek yok, sırası değişir.
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

from .pipeline import Notifier, ProcessedDisclosure, render_message
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
    assert item.decision is not None
    return (
        -sort_metric(item),
        -item.decision.impact,
        item.ticker or "~",
        item.disclosure.published_at,
        item.disclosure.index,
    )


def _dedupe(results: Sequence[ProcessedDisclosure]) -> tuple[list[ProcessedDisclosure], int]:
    """Aynı (index, hisse) tekrarını tekilleştirir; en yüksek skorlu kalır."""
    best: dict[tuple[int, str], ProcessedDisclosure] = {}
    duplicates = 0
    for item in results:
        key = (item.disclosure.index, item.ticker or "")
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        duplicates += 1
        if item.decision and current.decision:
            if item.decision.push_score > current.decision.push_score:
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
        if item.decision is None:
            continue
        if item.decision.action in {"push", "push_uncertain"}:
            pushable.append(item)
        elif item.decision.action == "digest":
            digestable.append(item)

    pushable.sort(key=_sort_key)
    digestable.sort(key=_sort_key)

    used: dict[str, int] = {}
    pushes: list[ProcessedDisclosure] = []
    demoted: list[ProcessedDisclosure] = []
    for item in pushable:
        ticker = item.ticker or "?"
        seen = used.get(ticker, 0)
        if seen >= budget_per_ticker:
            demoted.append(
                replace(item, notes=item.notes + (BUDGET_NOTE.format(n=seen + 1),))
            )
            continue
        used[ticker] = seen + 1
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


def _one_line(item: ProcessedDisclosure) -> str:
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
    return " · ".join(parts)


def render_digest(
    ranked: Ranked,
    *,
    day: date,
    max_items: int = 12,
    title: str = "GÜNLÜK ÖZET",
) -> str | None:
    """Özet mesajı. Gönderilecek olay yoksa `None` (boş mesaj atılmaz)."""
    if not ranked.digest:
        return None

    shown = ranked.digest[:max_items]
    lines = [
        f"{title} · {day.strftime('%d.%m.%Y')}",
        f"{len(ranked.digest)} olay · {len(ranked.pushes)} push ayrıca gönderildi",
        "",
    ]
    for i, item in enumerate(shown, 1):
        ticker = item.ticker or "?"
        subject = item.disclosure.subject.strip()
        lines.append(f"{i}. {ticker} · {subject}")
        lines.append(f"   {_one_line(item)}")

    rest = ranked.digest[max_items:]
    if rest:
        listed = ", ".join(f"{(i.ticker or '?')} {i.decision.push_score:.2f}" for i in rest[:5])
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

    @property
    def ok(self) -> bool:
        return self.pushed_failed == 0 and not self.digest_failed

    def summary(self) -> str:
        lines = [
            f"push {self.pushed_ok}/{self.pushed_ok + self.pushed_failed}",
            f"özet {'gönderildi' if self.digest_sent else 'yok'}",
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

    Sıra **kasıtlı**: en önemli push önce gider; özet en sona kalır (kullanıcı
    gün içinde tek tek bildirim, gün sonunda tek özet görür).
    """
    ranked = rank(results, budget_per_ticker=cfg.max_push_per_ticker_per_day)
    ok = 0
    failed = 0
    for item in ranked.pushes:
        key = f"{item.disclosure.index}:{item.ticker or '?'}"
        if await notifier.send(render_message(item), key=key):
            ok += 1
        else:
            failed += 1

    text = render_digest(ranked, day=day, max_items=max_items)
    digest_sent = False
    digest_failed = False
    if text is not None:
        if await notifier.send(text, key=f"digest:{day.isoformat()}"):
            digest_sent = True
        else:
            digest_failed = True

    return DispatchReport(
        pushed_ok=ok,
        pushed_failed=failed,
        digest_sent=digest_sent,
        digest_failed=digest_failed,
        ranked=ranked,
        digest_text=text,
    )
