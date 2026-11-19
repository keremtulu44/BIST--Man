"""Tek dosya birleşme paketi: `bistman` olmadan yalıtılmış ortamda tam çalışma testi."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "kap"


def test_single_file_bundle_runs_standalone_without_package_or_legacy_code(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import tek_dosya_uret

    source = tek_dosya_uret.build_bundle()
    tree = ast.parse(source)

    # 1) Dış bağımlılık yalnız standart kütüphane + httpx olmalı; eski motor sızmamalı.
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            external.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
            external.add(node.module.split(".")[0])
    assert "bistman" not in external
    non_stdlib = external - sys.stdlib_module_names
    assert non_stdlib == {"httpx"}

    for forbidden in ("TelegramNotifier", "ScoringConfig", "dispatch", "EventStudy", "LLMClient", "score_disclosure"):
        assert f"def {forbidden}" not in source and f"class {forbidden}" not in source

    # 2) `bistman` paketine erişim kapalıyken tek dosya doğrudan yüklenip çalışabilmeli.
    bundle_path = tmp_path / "kap_baglam.py"
    bundle_path.write_text(source, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("kap_baglam_standalone", bundle_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)

    company_html = (
        '<html><script>self.__next_f.push([1,"'
        '{\\"mkkMemberOid\\":\\"8acae2c575a1b5b60177de29c7177130\\",'
        '\\"kapMemberTitle\\":\\"MATR\\u0130KS F\\u0130NANSAL TEKNOLOJ\\u0130LER A.\\u015e.\\",'
        '\\"relatedMemberTitle\\":\\"\\",\\"stockCode\\":\\"MTRKS\\"}'
        '"])</script></html>'
    )
    light_bytes = (FIX / "list_light_live.json").read_bytes()
    detail_bytes = (FIX / "detail_1699030_live.json").read_bytes()
    observed = datetime(2026, 11, 17, 12, 45, tzinfo=timezone.utc)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tr/bist-sirketler":
            return httpx.Response(200, text=company_html)
        if request.url.path.endswith("/list/light"):
            return httpx.Response(200, content=light_bytes)
        if request.url.path.endswith("/1699030"):
            return httpx.Response(200, content=detail_bytes)
        return httpx.Response(404)

    db = tmp_path / "tek_dosya.sqlite"

    async def run_e2e() -> None:
        async with mod.KapClient(transport=httpx.MockTransport(handler)) as kap:
            service = mod.KapCollectorService(
                db, universe=["MTRKS", "THYAO"], kap=kap,
                probe_opaque=False, clock=lambda: observed,
            )
            report = await service.run_once()
            assert report.total_rows == 397
            assert report.counts["hazir"] == 1

    asyncio.run(run_e2e())

    # Grafik alarmı 14:53 yayınından sonra ama 15:45 gözleminden önceyse lookahead yapmaz.
    istek_erken: dict[str, object] = {"hisse": "MTRKS"}
    erken = mod.kap_baglami_ekle(
        istek_erken, db, "MTRKS", datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc),
    )
    assert erken is not None and erken["durum"] == "yok" and "kap_baglami" not in istek_erken

    # Gözlem anında ise doğrulanmış MTRKS açıklaması doğrudan ana sistemin isteğine eklenir.
    istek_hazir: dict[str, object] = {"hisse": "MTRKS"}
    hazir = mod.kap_baglami_ekle(istek_hazir, db, "MTRKS", observed)
    assert hazir is not None and hazir["durum"] == "var"
    assert istek_hazir["kap_baglami"]["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/1699030"
    assert "yaptırım kararı bulunmamaktadır." in istek_hazir["kap_baglami"]["olay"]
