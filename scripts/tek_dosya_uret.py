"""Ana sistemle tek uygulama / tek Render birleşmesi için tek dosya üretici.

Yalnız doğrulanmış KAP bağlam çekirdeğini paketler (standart kütüphane + `httpx`).
Eski skor motoru, Telegram/push, özet, fiyat indirme, olay çalışması ve ikinci AI
çağrısı dahil EDİLMEZ.

Kullanım:
    python scripts/tek_dosya_uret.py kap_baglam.py
    python scripts/tek_dosya_uret.py kap_baglam.py --dogrula
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Bağımlılık sırasına göre dizilmiş yalın çekirdek modüller (çakışma yok).
CORE_MODULES: tuple[str, ...] = (
    "bistman/parsing/text.py",
    "bistman/parsing/dates.py",
    "bistman/parsing/money.py",
    "bistman/parsing/kap_table.py",
    "bistman/parsing/evidence.py",
    "bistman/sources/base.py",
    "bistman/triage.py",
    "bistman/ticker_map.py",
    "bistman/sources/kap.py",
    "bistman/sources/kap_companies.py",
    "bistman/fundamentals.py",
    "bistman/context_packet.py",
    "bistman/alarm_context.py",
    "bistman/collector.py",
)

PUBLIC_EXPORTS: tuple[str, ...] = (
    # Ana sistem alarm okuma sözleşmesi
    "DEFAULT_MAX_AGE",
    "AlarmArchive",
    "ReviewedReading",
    "open_archive",
    "kap_baglami_ekle",
    # Arka plan sessiz KAP toplama sözleşmesi
    "KapCollectorService",
    "ScanReport",
    "EvidenceProposal",
    "EvidenceReviewer",
    "extract_verified_evidence",
    "ingest_feed",
    "sync_once",
    # Kaynak, şirket listesi ve isteğe bağlı mali tablo tipleri
    "KapClient",
    "KapError",
    "KapParseError",
    "KapUnavailable",
    "CompanyEntry",
    "CompanyMapError",
    "fetch_companies",
    "parse_companies",
    "build_company_resolver",
    "select_primary_ticker",
    "TickerResolver",
    "DisclosureStub",
    "DisclosureDetail",
    "FundamentalRow",
    "PublishedRow",
    "ArchiveCandidate",
    "PacketHeld",
    "build_packet",
    "make_candidate",
    "parse_light",
    "parse_detail",
    "parse_history",
    "body_text",
)

_HEADER = '''"""BIST-Man tek dosya KAP alarm bağlam modülü (otomatik üretildi).

Görev ayrımı:
  - Arka planda: `KapCollectorService(db_yolu, universe=...).run_forever()`
    KAP listesini ortak okur, tek hisse ve tam cümle kanıtını doğrular, SQLite
    (WAL) arşivine gerçek gözlem saatiyle (`known_at`) sessizce yazar.
    Alarm üretmez, mesaj göndermez, ikinci AI çağrısı açmaz.
  - Grafik alarmı tetiklendiğinde: `kap_baglami_ekle(istek, db_yolu, hisse, alarm_zamani)`
    arşivi salt okunur sorgular; uygun kanıt varsa mevcut yorum isteğine ekler.

Dış bağımlılık: yalnız Python 3.11+ standart kütüphanesi ve `httpx`.
"""

from __future__ import annotations
'''


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_type_checking_if(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    )


def _strip_module(source: str) -> tuple[list[str], str]:
    """Modül başı docstring, `__all__` ve tüm göreli importları güvenle ayıklar."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    ext_imports: list[str] = []
    replacements: list[tuple[int, int, str]] = []

    def mark_remove(node: ast.stmt, replacement: str = "") -> None:
        assert node.end_lineno is not None
        replacements.append((node.lineno, node.end_lineno, replacement))

    for idx, node in enumerate(tree.body):
        if idx == 0 and _is_docstring(node):
            mark_remove(node)
        elif isinstance(node, ast.Import):
            ext_imports.append(ast.get_source_segment(source, node) or "")
            mark_remove(node)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module != "__future__":
                ext_imports.append(ast.get_source_segment(source, node) or "")
            mark_remove(node)
        elif _is_type_checking_if(node):
            mark_remove(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                mark_remove(node)

    # Fonksiyon içi göreli importlar (tek dosyada sembol zaten üstte tanımlı).
    top_set = set(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node not in top_set:
            indent = " " * node.col_offset
            mark_remove(node, f"{indent}pass\n")

    # Çakışmayan satır aralıklarını sondan başa uygula.
    out = list(lines)
    for start, end, rep in sorted(replacements, key=lambda r: r[0], reverse=True):
        out[start - 1 : end] = [rep] if rep else []

    body = "".join(out).strip()
    return ext_imports, body


def _merge_imports(raw_imports: list[str]) -> str:
    plain: set[str] = set()
    from_map: dict[str, set[str]] = {}
    for stmt in raw_imports:
        if not stmt:
            continue
        mod_node = ast.parse(stmt).body[0]
        if isinstance(mod_node, ast.Import):
            for alias in mod_node.names:
                plain.add(f"{alias.name} as {alias.asname}" if alias.asname else alias.name)
        elif isinstance(mod_node, ast.ImportFrom) and mod_node.module:
            bucket = from_map.setdefault(mod_node.module, set())
            for alias in mod_node.names:
                if alias.name == "TYPE_CHECKING":
                    continue
                bucket.add(f"{alias.name} as {alias.asname}" if alias.asname else alias.name)

    rendered = [f"import {name}" for name in sorted(plain)]
    for module in sorted(from_map):
        names = sorted(from_map[module])
        if names:
            rendered.append(f"from {module} import {', '.join(names)}")
    return "\n".join(rendered)


def build_bundle() -> str:
    """Tek dosyalık Python kaynak kodunu üretir ve sözdizimini doğrular."""
    all_imports: list[str] = []
    sections: list[str] = []
    defined: dict[str, str] = {}

    for rel in CORE_MODULES:
        path = SRC / rel
        raw = path.read_text(encoding="utf-8")
        tree = ast.parse(raw, filename=rel)
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name) and t.id != "__all__")
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
            for name in names:
                if name in defined:
                    raise RuntimeError(f"Sembol çakışması: {name} ({defined[name]} ve {rel})")
                defined[name] = rel

        imports, body = _strip_module(raw)
        all_imports.extend(imports)
        sections.append(f"# {'=' * 74}\n# Kaynak: {rel}\n# {'=' * 74}\n\n{body}")

    exports_block = "__all__ = [\n" + "".join(f'    "{n}",\n' for n in PUBLIC_EXPORTS) + "]\n"
    bundle = (
        f"{_HEADER}\n"
        f"{_merge_imports(all_imports)}\n\n"
        f"{exports_block}\n\n"
        + "\n\n\n".join(sections)
        + "\n"
    )
    # Üretilen dosyanın geçerli Python olduğunu ve hiçbir göreli/paket importu kalmadığını denetle.
    parsed = ast.parse(bundle, filename="kap_baglam.py")
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0 or (node.module and node.module.startswith("bistman")):
                raise RuntimeError(f"Artık paket importu kaldı: {ast.dump(node)}")
        elif isinstance(node, ast.Import):
            if any(a.name.startswith("bistman") for a in node.names):
                raise RuntimeError(f"Artık paket importu kaldı: {ast.dump(node)}")
    missing = [name for name in PUBLIC_EXPORTS if name not in defined]
    if missing:
        raise RuntimeError(f"Eksik dışa aktarım sembolleri: {missing}")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cikti", type=Path, help="üretilecek tek Python dosyasının yolu")
    parser.add_argument("--dogrula", action="store_true",
                        help="üretilen dosyayı yalıtılmış ortamda içe aktarıp duman testi çalıştır")
    args = parser.parse_args()

    code = build_bundle()
    args.cikti.parent.mkdir(parents=True, exist_ok=True)
    args.cikti.write_text(code, encoding="utf-8")

    if args.dogrula:
        import importlib.util
        import tempfile
        from datetime import datetime, timezone

        spec = importlib.util.spec_from_file_location("_kap_baglam_tek", args.cikti)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite"
            at = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
            sale = "Şirketimiz ile T.C. Sağlık Bakanlığı arasında 90 milyon TL bedelli hizmet satış sözleşmesi imzalanmıştır."
            stub = mod.DisclosureStub(1, at, "Örnek A.Ş.", "Yeni İş İlişkisi", summary=sale, tickers=("ORNEK",))
            with mod.open_archive(db, writable=True) as arc:
                assert arc.record(
                    mod.make_candidate(stub, "ORNEK"),
                    origin="kap_arsiv", known_at=at, topic="satis_sozlesmesi", quote=sale,
                ) == "hazir"
            istek: dict[str, object] = {}
            res = mod.kap_baglami_ekle(istek, db, "ORNEK", at)
            assert res is not None and res["durum"] == "var" and "kap_baglami" in istek

    print(
        f"{args.cikti} üretildi ({len(code.splitlines())} satır, "
        f"{len(CORE_MODULES)} çekirdek modül, dış bağımlılık: yalnız httpx)"
    )


if __name__ == "__main__":
    main()
