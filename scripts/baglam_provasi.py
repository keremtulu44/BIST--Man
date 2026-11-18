"""Kanıt paketi provası — python scripts/baglam_provasi.py (ağ ve anahtar yok).

Gerçek: kaydedilmiş KAP listesi ve MTRKS detayı. Sentetik: şirket sözleşmesi,
üretim duruşu, döviz ve zaman damgalı iki bilanço. Bunlar güncel haber değildir.
Konu/alıntı ve referans yorum elle seçilmiştir; yorum paketin girdisi değildir.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.alarm_context import ReviewedReading  # noqa: E402
from bistman.context_packet import (  # noqa: E402
    MAX_CHARS, Origin, PacketHeld, PublishedRow, Topic, build_packet, compact_json,
)
from bistman.digest import dispatch  # noqa: E402
from bistman.notify import FakeNotifier  # noqa: E402
from bistman.parsing.kap_table import body_text  # noqa: E402
from bistman.parsing.money import find_amounts  # noqa: E402
from bistman.pipeline import ProcessedDisclosure, process_batch  # noqa: E402
from bistman.scoring import ScoringConfig  # noqa: E402
from bistman.sources.base import DisclosureDetail, DisclosureStub, FundamentalRow  # noqa: E402
from bistman.sources.kap import parse_detail, parse_light  # noqa: E402
from bistman.ticker_map import TickerResolver  # noqa: E402
from bistman.triage import Route, route  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "kap"
AT = datetime(2026, 11, 17, 10, tzinfo=timezone.utc)
KNOWN = PublishedRow(
    FundamentalRow("ORNEK", "2026-06-30", revenue_ttm=1_000_000_000,
                   net_income_ttm=90_000_000, equity=700_000_000, source="senaryo:mali-tablo-1"),
    datetime(2026, 8, 12, 9, tzinfo=timezone.utc),
)
LATER = PublishedRow(
    replace(KNOWN.row, period_end="2026-09-30", revenue_ttm=2_000_000_000, source="senaryo:mali-tablo-2"),
    AT + timedelta(hours=2),
)


@dataclass(frozen=True)
class Case:
    name: str
    origin: Origin
    stub: DisclosureStub
    topic: Topic
    detail: DisclosureDetail | None
    quote: str
    reference: ReviewedReading  # editörün örnek okuması; build_packet'e verilmez


@dataclass(frozen=True)
class Outcome:
    case: Case
    processed: ProcessedDisclosure
    packet: dict[str, object] | None
    held: str | None


def cases(by_id: dict[int, DisclosureStub]) -> list[Case]:
    stub = DisclosureStub(1, AT, "Örnek Sanayi A.Ş.", "Yeni Satış Sözleşmesi",
                          disclosure_class="ODA", tickers=("ORNEK",))
    sale = "Şirketimiz bu proje kapsamında 24 ayda teslim edilecek ürünler için 200 milyon TL bedelli satış sözleşmesi imzalamıştır."
    stop = "Döküm hattında planlanmamış bakım nedeniyle üretime ara verilmiştir. Yeniden başlama tarihi henüz belirlenmemiştir."
    fx = "Şirketimiz yurt dışındaki müşterisiyle 10 milyon EUR bedelli satış sözleşmesi imzalamıştır."
    stop_stub = replace(stub, index=2, subject="Üretime Ara Verilmesi")
    fx_stub = replace(stub, index=3)
    index = by_id[1698956]
    detail = parse_detail((FIXTURE / "detail_1699030_live.json").read_bytes())
    body = body_text(detail.body_html)
    # Sadece son cümleyi almak 'söz konusu inceleme'nin kapsamını kaybettirir.
    a = body.index("Sermaye Piyasası Kurulunun 2024/35")
    b = body.index("bulunmamaktadır.", a) + len("bulunmamaktadır.")
    return [
        Case("Satış sözleşmesi", "sentetik", stub, "satis_sozlesmesi",
             DisclosureDetail(stub, "Ana projenin toplam bütçesi 2 milyar TL'dir. " + sale, 0), sale,
             ReviewedReading("İki yıllık sipariş, bir yıllık satışların beşte biri büyüklüğünde.",
                             "Teslimatlar satışları destekleyebilir; bedelin tamamı bu yılın geliri veya kârı değildir.")),
        Case("Üretim duruşu", "sentetik", stop_stub,
             "uretim_durusu", DisclosureDetail(stop_stub, stop, 0), stop,
             ReviewedReading("Döküm hattındaki duruş teslimatları geciktirebilir.",
                             "Süre ve hattın kapasitedeki payı olmadan şirket geneli için kayıp hesaplanamaz.")),
        Case("Endeks gündemi", "kap_arsiv", index, "endeks_parametresi", None, index.subject,
             ReviewedReading("Fiili dolaşım oranının değişmesi ilgili payların endeks ağırlıklarını etkileyebilir.",
                             "Başlıkta hangi payların değiştiği ve yürürlük tarihi yok; belirli bir hisseye yön atfedilemez.")),
        Case("Şirketin söylentiye yanıtı", "kap_arsiv", detail.stub, "sirket_aciklamasi", detail, body[a:b],
             ReviewedReading("MTRKS, SPK’nın 2024/35 sayılı bültenindeki incelemede kendisine veya yöneticilerine "
                             "yönelik bir yaptırım açıklanmadığını belirtti.",
                             "Şirket, payındaki işlemlere ilişkin eski kararları açıklığa kavuşturuyor; yeni bir ceza bildirmiyor.")),
        Case("Kur yok", "sentetik", fx_stub, "satis_sozlesmesi", DisclosureDetail(fx_stub, fx, 0), fx,
             ReviewedReading("Döviz cinsinden sipariş alınması satış görünürlüğünü destekleyebilir.",
                             "Haber anına ait kur yokken TL ciroya oran verilemez.")),
        Case("İhale sonucu başlığı (satış için negatif kontrol)", "kap_arsiv", by_id[1698981],
             "satis_sozlesmesi", None, by_id[1698981].summary or "",
             ReviewedReading("FONET başlığından ihalenin kazanıldığı veya kaybedildiği çıkmıyor.",
                             "Sonuç cümlesi için detay gerekli.")),
        Case("Kapsamı belirsiz hak kullanımı (negatif kontrol)", "kap_arsiv", by_id[1698691],
             "endeks_parametresi", None, "Hak Kullanımı",
             ReviewedReading("Yayıncı Borsa İstanbul.",
                             "‘Hak Kullanımı’ özeti piyasa genelinde kural değiştiğinin kanıtı değil.")),
    ]


def scan_feed(rows: list[DisclosureStub]) -> tuple[Counter, int]:
    routes = Counter()
    with_amount = 0
    for row in rows:
        r = route(row.disclosure_class, row.subject, row.summary)
        routes[r] += 1
        if r is Route.PIPELINE and find_amounts(f"{row.subject} {row.summary or ''}"):
            with_amount += 1
    return routes, with_amount


async def process_cases(batch: list[Case]) -> list[ProcessedDisclosure]:
    """Ortak fixture hazırlığı: burada alıcı/gönderim yok."""
    details = {c.detail.stub.index: c.detail for c in batch if c.detail}

    async def body_provider(stub: DisclosureStub) -> DisclosureDetail | None:
        return details.get(stub.index)

    return await process_batch(
        [c.stub for c in batch], cfg=ScoringConfig.load(), body_provider=body_provider,
        fundamentals={"ORNEK": (LATER, KNOWN)}, notifier=None,
        resolver=TickerResolver.from_pairs([("FONET", "FONET BİLGİ TEKNOLOJİLERİ A.Ş.")]),
    )


async def replay(batch: list[Case]) -> list[Outcome]:
    processed = await process_cases(batch)
    result = []
    for case, item in zip(batch, processed, strict=True):
        try:
            packet = build_packet(item, origin=case.origin, topic=case.topic, quote=case.quote,
                                  detail=case.detail, statements=(LATER, KNOWN))
        except PacketHeld as exc:
            result.append(Outcome(case, item, None, str(exc)))
        else:
            result.append(Outcome(case, item, packet, None))
    return result


def main() -> None:
    feed = parse_light((FIXTURE / "list_light_live.json").read_bytes())
    routes, with_amount = scan_feed(feed.items)
    outcomes = asyncio.run(replay(cases({d.index: d for d in feed.items})))
    print("KAP ARŞİVİ · 17.11.2026 · canlı akış değil")
    print(f"{len(feed.items)} satır, {len(feed.rejected)} bozuk · rutin {routes[Route.ROUTINE]}, "
          f"mekanik {routes[Route.MECHANICAL]}, aday {routes[Route.PIPELINE]} · "
          f"başlık/özette tutar çıkarılan aday {with_amount}")
    sizes = []
    for o in outcomes:
        print(f"\n{o.case.name} [{o.case.origin} #{o.case.stub.index}] · mevcut hat: "
              f"{o.processed.planned_action or o.processed.status}")
        if o.processed.features and o.processed.features.ratio_revenue is not None:
            print(f"Mevcut hattın tutar/ciro oranı: %{o.processed.features.ratio_revenue:g}")
        if o.packet is None:
            print(f"BEKLET · {o.held} · API paketi yok")
        else:
            wire = compact_json(o.packet)
            sizes.append(len(wire))
            print(f"HAZIR · {len(wire)} karakter\n{wire}")
        ref = o.case.reference
        print(f"Referans okuma (elle yazıldı, model çıktısı değil): {ref.event} {ref.significance}")
    # Paketler gönderilmez. Ayrı kural hattının planını bir sahte alıcıda doğrula.
    notifier = FakeNotifier()
    report = asyncio.run(dispatch((o.processed for o in outcomes), notifier, day=AT.date(), cfg=ScoringConfig.load()))
    print(f"\nKURAL HATTI · sahte alıcı: {report.summary()}")
    for key, message in notifier.sent:
        print(f"[{key}]\n{message}")
    size_note = (f"medyan {median(sizes):g}, en büyük {max(sizes)}/{MAX_CHARS} karakter"
                 if sizes else "ölçülecek paket yok")
    print(f"\n{len(sizes)} hazır, {len(outcomes) - len(sizes)} bekletildi · {size_note} · API çağrısı 0")


if __name__ == "__main__":
    main()
