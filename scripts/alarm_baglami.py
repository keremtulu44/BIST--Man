"""Arşivi bir kere hazırla, yalnız dış alarm geldiğinde sorgula. İki komut da ağsız."""

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
                                    reviewed=case.reference if case.topic == "sirket_aciklamasi" else None)
            counts[result] += 1
    return {"arsiv_kesiti": "17.11.2026", "incelenen": len(selected), "sonuclar": dict(counts),
            "varsayim": "Yalnız demoda ilk kullanılabilir an = KAP yayını; alım gecikmesi ölçülmedi."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("hazirla-demo", help="yerel, incelenmiş KAP örneklerini yükle (canlı veri değil)")
    seed.add_argument("arsiv", type=Path)
    query = commands.add_parser("sorgula", help="mevcut arşivden tek alarm için JSON oku")
    query.add_argument("arsiv", type=Path)
    query.add_argument("hisse")
    query.add_argument("alarm_zamani", type=datetime.fromisoformat, help="saat dilimli ISO 8601")
    args = parser.parse_args()
    try:
        if args.command == "hazirla-demo":
            result = load_demo(args.arsiv)
        else:
            with open_archive(args.arsiv) as archive:
                result = archive.alarm_baglami(args.hisse, args.alarm_zamani)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Hata: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
