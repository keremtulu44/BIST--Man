"""Veri kaynağı sözleşmeleri (Protocol) ve iç veri modeli.

Neden Protocol: gerçek kaynak (KAP), fixture kaynağı ve Faz 2'de DB kaynağı
aynı arayüzü uygular. Testler ağ olmadan `FixtureFeed` ile çalışır
(MIMARI.md §6.3 — replay'i mümkün kılan şey bu ayrım).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class DisclosureStub:
    """Liste ucundan gelen hafif bildirim kaydı (gövde YOK).

    `tickers` boş olabilir: /api/disclosure/list/light ticker vermiyor,
    sadece `title` (şirket unvanı) var. Unvan→kod eşlemesi ayrı katmanda yapılır.
    """

    index: int
    """/api'deki `disclosureIndex` — cursor ve dedup anahtarı."""

    published_at: datetime
    """aware UTC. KAP TRT verir, kaynak katmanı çevirir."""

    title: str
    """Şirket unvanı (light feed'de ticker yerine bu gelir)."""

    subject: str
    """Bildirim konusu — triyajın anahtarı."""

    summary: str | None = None
    disclosure_class: str | None = None
    """'ODA' | 'FR' | 'DKB' | None."""
    tickers: tuple[str, ...] = ()
    """Dolu ise yapısal hisse kodları (detay ucu / geçmiş tablosu)."""
    member_oid: str | None = None
    """Şirketin KAP üye OID'i — geçmiş sorgusu ve detay için gerekir."""


@dataclass(frozen=True, slots=True)
class FundamentalRow:
    """Finansal snapshot (append-only yazılır)."""

    ticker: str
    period_end: str  # ISO date; ayrıştırma sınırında tek forma indirgenir
    statement: str = "consolidated"  # 'consolidated' | 'solo'
    statement_type: str = "industrial"  # 'industrial' | 'bank'  (banka -> UFRS şablonu)
    revenue_ttm: float | None = None
    net_income_ttm: float | None = None
    equity: float | None = None
    currency: str = "TRY"
    source: str = "borsapy/isyatirim"
    flags: tuple[str, ...] = field(default_factory=tuple)
    """ttm_gap, mixed_restatement_vintage, bank_template_mismatch ..."""


@runtime_checkable
class DisclosureFeed(Protocol):
    """Piyasa geneli bildirim akışı (borsapy'de YOK, bizim katman)."""

    async def latest(self) -> list[DisclosureStub]:
        """Bugünün/ son çekilen akış. Boş liste = 'haber yok' DEĞİL, şüpheli."""
        ...

    async def history(self, member_oid: str) -> list[DisclosureStub]:
        """Bir şirketin geçmiş bildirimleri (backfill: 50 istek ~ 1 yıl)."""
        ...

    async def detail(self, index: int) -> DisclosureStub:
        """Tek bildirim detayı — yapısal `tickers` ve gövde için."""
        ...


@runtime_checkable
class FundamentalsSource(Protocol):
    async def snapshot(self, ticker: str, statement_type: str) -> FundamentalRow | None:
        """`statement_type`: 'industrial' | 'bank'. Banka -> financial_group='UFRS'."""
        ...


@runtime_checkable
class UniverseSource(Protocol):
    async def tickers(self) -> Sequence[str]:
        """Endeks bileşenleri (XU050). Boş dönerse config fallback'i devreye girer."""
        ...


@dataclass(frozen=True, slots=True)
class DisclosureDetail:
    """Detay ucunun (`/api/notification/attachment-detail/{idx}`) iç modeli."""

    stub: DisclosureStub
    body_html: str
    attachment_count: int
    is_late: bool = False


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Ayrıştırma sonucu: sessizce satır düşürmemek için reddedilenleri de taşır.

    `items` boş + `rejected` boş ise bu "haber yok" DEĞİL, **şüpheli** demektir
    (MIMARI.md §2 — sessiz ölüm). Çağıran katman boşluğu firehose ile çapraz
    kontrol etmekle yükümlü.
    """

    items: list[DisclosureStub]
    rejected: list[tuple[int, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.items)
