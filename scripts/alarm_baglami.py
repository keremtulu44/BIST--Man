"""Arşivi hazırla veya güncelle, yalnız dış alarm geldiğinde salt okunur sorgula."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bistman.alarm_context import open_archive  # noqa: E402


def load_demo(path: Path) -> dict:
    # Fixture yükleyici yalnız açıkça istendiğinde çalışır, gerçek sorgunun parçası değildir.
    from baglam_provasi import FIXTURE, cases, process_cases
    from bistman.sources.kap import parse_light

    rows = parse_light((FIXTURE / "list_light_live.json").read_bytes()).items
    selected = [c for c in cases({r.index: r for r in rows}) if c.origin == "kap_arsiv"]
    processed = asyncio.run(process_cases(selected))
    counts: Counter[str] = Counter()
    # Varsayılan bilinirlik saatiyle var olan (belki gerçek) arşivi geri tarihleme.
    path.touch(exist_ok=False)
    with open_archive(path, writable=True) as archive:
        for case, item in zip(selected, processed, strict=True):
            # Arşivde gerçek ilk görülme saati yok: sadece bu yeniden oynatmada
            # kanıtı KAP yayını anında kullanılabilir varsayıyoruz.
            result = archive.record(item, origin=case.origin, topic=case.topic, quote=case.quote,
                                    detail=case.detail, known_at=case.stub.published_at,
                                    replay=True,
                                    reviewed=case.reference if case.topic == "sirket_aciklamasi" else None)
            counts[result] += 1
    return {"arsiv_kesiti": "17.11.2026", "incelenen": len(selected), "sonuclar": dict(counts),
            "varsayim": "Yalnız demoda ilk kullanılabilir an = KAP yayını; alım gecikmesi ölçülmedi."}


async def _scan_live_async(
    path: Path, universe_file: Path | None, probe_limit: int, warmup: bool,
) -> dict:
    from bistman.collector import KapCollectorService

    universe: list[str] | None = None
    if universe_file is not None:
        universe = [
            line.strip().upper()
            for line in universe_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    service = KapCollectorService(
        path,
        universe=universe,
        probe_opaque=True,
        probe_unresolved=probe_limit > 0,
        max_unresolved_probes=max(25, probe_limit) if probe_limit > 0 else 30,
    )
    if warmup and universe:
        await service.warmup(universe)
    report = await service.run_once()
    return {
        "toplam_satir": report.total_rows,
        "reddedilen_satir": report.rejected_rows,
        "detay_istegi": report.detail_requests,
        "denetlenmemis_kurum_satiri": report.unprobed_opaque,
        "sonuclar": report.counts,
        "mali_tablo_yenileme_adaylari": list(report.fundamentals_refresh),
        "son_tarama": report.scanned_until.isoformat() if report.scanned_until else None,
        "supheli_bos": report.suspicious_empty,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("hazirla-demo", help="yerel, incelenmiş KAP örneklerini yükle (canlı veri değil)")
    seed.add_argument("arsiv", type=Path)
    scan = commands.add_parser("tara-canli", help="KAP listesini tek tur sessizce tara ve arşivi güncelle")
    scan.add_argument("arsiv", type=Path)
    scan.add_argument("--evren", type=Path, default=None, help="isteğe bağlı hisse listesi dosyası (satır başına kod)")
    scan.add_argument("--kimlik-kontrol-siniri", type=int, default=0,
                      help="unvanı eşleşmeyen adaylar için en çok detay kontrol sayısı")
    scan.add_argument("--isindir", action="store_true",
                      help="--evren hisseleri için son 7 günlük üye geçmişini de tara")
    query = commands.add_parser("sorgula", help="mevcut arşivden tek alarm için JSON oku")
    query.add_argument("arsiv", type=Path)
    query.add_argument("hisse")
    query.add_argument("alarm_zamani", type=datetime.fromisoformat, help="saat dilimli ISO 8601")
    args = parser.parse_args()
    try:
        if args.command == "hazirla-demo":
            result = load_demo(args.arsiv)
        elif args.command == "tara-canli":
            from bistman.sources.kap import KapError
            from bistman.sources.kap_companies import CompanyMapError

            try:
                result = asyncio.run(
                    _scan_live_async(args.arsiv, args.evren, args.kimlik_kontrol_siniri, args.isindir)
                )
            except (KapError, CompanyMapError) as exc:
                parser.exit(2, f"Hata: {exc}\n")
        else:
            with open_archive(args.arsiv) as archive:
                result = archive.alarm_baglami(args.hisse, args.alarm_zamani)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Hata: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
