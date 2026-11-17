"""TTM DOĞRULAMA 2 — kümülatif hipotezini kesinleştirir + eksik alanları bulur.

Birinci script "TTM == 4 çeyrek toplamı: EVET" dedi. Bu **tutarlılık** kanıtıydı,
**doğruluk** değil. Sayılar kümülatif (YTD) olduğu için aslında 2x hata var:
THYAO TTM 2.72 trilyon çıktı, gerçek ~1.36 trilyon.

Bu script üç şeyi bitirir:

  1. KÜMÜLATİF Mİ? — 8 çeyrek + yıllık tablo yan yana. Kesin kanıt:
     yıllık[2025] == 2025Q4  ->  kümülatif
     yıllık[2025] == Σ4 çeyrek ->  tekil

  2. DOĞRU TTM — FY + YTD(güncel) - YTD(geçen yıl aynı dönem).
     borsapy'nin toplamıyla oranı yazılır (beklenen ~2x).

  3. EKSİK ALANLAR — `fast_info` BOŞ döndü (`{}`). PD/fiyat nerede?
     `info` anahtarları, `history()` son satırı, `balance_sheet` denenir.
     Ayrıca tüm kalem adları dökülür (satır seçici tablosunu kesinleştirmek için).

Çalıştır:  python scripts\\dogrulama\\ttm_dogrula2.py
Çıktı:     docs/dogrulama-sonuc2.json   <- bunu gönder
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs" / "dogrulama-sonuc2.json"
RESULT: dict = {
    "zaman_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "python": sys.version.split()[0],
    "kontroller": [],
}

FOLD = str.maketrans({"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                      "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
                      "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u"})

ROLES = {
    "industrial": {
        "revenue": ("satis gelirleri", "hasilat", "net satislar"),
        "net_income": ("net donem kar", "donem kari", "ana ortaklik payi"),
    },
    "bank": {
        "revenue": ("faiz gelirleri",),
        "net_income": ("net donem kar", "donem kari"),
    },
}


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).translate(FOLD).lower()).strip()


def log(status: str, title: str, detail: str = "") -> None:
    icon = {"OK": "[OK]  ", "WARN": "[UYARI]", "FAIL": "[HATA]", "INFO": "[BILGI]"}[status]
    print(f"{icon} {title}")
    for line in str(detail).splitlines():
        if line:
            print(f"         {line}")
    RESULT["kontroller"].append({"durum": status, "baslik": title, "detay": str(detail)})


def find_row(df, keywords) -> str | None:
    """Roma rakamlı üst kalem tercih edilir (alt kalem tuzağı)."""
    best, best_score = None, 0
    for label in df.index:
        f = fold(label)
        if not any(k in f for k in keywords):
            continue
        score = 3
        if re.match(r"^[IVX]{1,5}\.\s", str(label).strip()):
            score += 4
        if re.match(r"^\d+(\.\d+)+", str(label).strip()):
            score -= 3
        if "diger" in f:
            score -= 2
        if score > best_score:
            best, best_score = str(label), score
    return best


def period(col) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})\s*Q([1-4])", str(col).upper().strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def check(bp, ticker: str, kind: str) -> None:
    print(f"\n=== {ticker} ({kind}) ===")
    t = bp.Ticker(ticker)
    kwargs = {"financial_group": "UFRS"} if kind == "bank" else {}

    try:
        quarterly = t.get_income_stmt(quarterly=True, last_n=8, **kwargs)
    except Exception as exc:
        log("FAIL", f"{ticker} çeyreklik tablo", f"{type(exc).__name__}: {exc}")
        return
    try:
        annual = t.get_income_stmt(**kwargs)
    except Exception as exc:
        log("WARN", f"{ticker} yıllık tablo yok", str(exc))
        annual = None

    cols = [str(c) for c in quarterly.columns]
    log("INFO", f"{ticker} çeyreklik sütunlar (8)", ", ".join(cols))

    # Kalem adlarını dök: satır seçici tablosunu kesinleştirmek için
    labels = [str(i) for i in quarterly.index]
    RESULT.setdefault("kalemler", {})[ticker] = labels
    log("INFO", f"{ticker} kalem sayısı", str(len(labels)))

    for role, keywords in ROLES[kind].items():
        label = find_row(quarterly, keywords)
        if not label:
            log("WARN", f"{ticker} {role} satırı bulunamadı", "")
            continue

        row = quarterly.loc[label]
        points = []
        for col, val in zip(quarterly.columns, row.tolist()):
            p = period(col)
            if p and val is not None:
                points.append((p[0], p[1], float(val)))
        points.sort()

        values = ", ".join(f"{y}Q{q}={v:,.0f}" for y, q, v in points)
        log("INFO", f"{ticker} {role} = {label!r}", values)

        # 1) KÜMÜLATİF Mİ? yıllık tablo ile karşılaştır
        annual_map = {}
        if annual is not None and label in [str(i) for i in annual.index]:
            arow = annual.loc[label]
            for col, val in zip(annual.columns, arow.tolist()):
                m = re.fullmatch(r"(\d{4})", str(col).strip())
                if m and val is not None:
                    annual_map[int(m.group(1))] = float(val)
        q4 = {y: v for y, q, v in points if q == 4}
        verdict = "bilinmiyor"
        for year, fy in annual_map.items():
            if year in q4 and abs(q4[year] - fy) / max(abs(fy), 1) < 0.005:
                verdict = f"KÜMÜLATİF (yıllık[{year}] == {year}Q4)"
                break
            four = [v for y, q, v in points if y == year]
            if len(four) == 4 and abs(sum(four) - fy) / max(abs(fy), 1) < 0.005:
                verdict = f"TEKİL (yıllık[{year}] == Σ4)"
                break
        log("OK" if verdict != "bilinmiyor" else "WARN", f"{ticker} {role} modu", verdict)
        RESULT.setdefault("mod", {}).setdefault(ticker, {})[role] = verdict

        # 2) DOĞRU TTM vs borsapy
        naive = sum(v for _, _, v in points[-4:])
        full_years = sorted(y for y, q, _ in points if q == 4)
        correct = None
        used = ""
        if full_years:
            fy_year = full_years[-1]
            fy = next(v for y, q, v in points if y == fy_year and q == 4)
            last_y, last_q, last_v = points[-1]
            if last_q == 4:
                correct, used = fy, f"{fy_year}Q4 (tam yıl)"
            else:
                prev = next(((y, q, v) for y, q, v in points
                             if y == last_y - 1 and q == last_q), None)
                if prev:
                    correct = fy + last_v - prev[2]
                    used = f"{fy_year}Q4 + {last_y}Q{last_q} - {last_y-1}Q{last_q}"
                else:
                    correct, used = fy, f"{fy_year}Q4 (geçen yıl aynı dönem yok)"
        if correct:
            ratio = naive / correct if correct else float("inf")
            log("OK", f"{ticker} {role} DOĞRU TTM",
                f"borsapy toplamı = {naive:,.0f}\ndoğru TTM      = {correct:,.0f}   [{used}]\n"
                f"borsapy fazla  = {ratio:.2f}x")
            RESULT.setdefault("ttm", {}).setdefault(ticker, {})[role] = {
                "label": label, "borsapy": naive, "dogru": correct, "oran": ratio, "formul": used,
            }

    # 3) PD / fiyat nerede?
    print(f"\n--- {ticker} piyasa verisi ---")
    for attr in ("fast_info", "info"):
        try:
            data = getattr(t, attr)
            if isinstance(data, dict):
                small = {k: data[k] for k in list(data)[:25]}
                log("OK" if data else "WARN", f"{ticker} {attr}",
                    f"{len(data)} anahtar: " + json.dumps(small, ensure_ascii=False, default=str)[:800])
                RESULT.setdefault("piyasa", {}).setdefault(ticker, {})[attr] = {
                    k: str(v) for k, v in small.items()
                }
        except Exception as exc:
            log("FAIL", f"{ticker} {attr}", f"{type(exc).__name__}: {exc}")
    try:
        hist = t.history(period="5d")
        tail = hist.tail(1).to_dict("records")
        log("OK", f"{ticker} history(5d) son satır",
            json.dumps(tail, ensure_ascii=False, default=str)[:500])
        RESULT.setdefault("piyasa", {}).setdefault(ticker, {})["history"] = tail
    except Exception as exc:
        log("FAIL", f"{ticker} history", f"{type(exc).__name__}: {exc}")


def main() -> int:
    try:
        import borsapy as bp
    except ImportError:
        print("[HATA] borsapy bu Python'da kurulu DEĞİL.")
        print(f"       Python: {sys.executable}")
        print("       Doğru komut (aktivasyon gerekmez):")
        print(f'       "{sys.executable}" -m pip install "borsapy==0.11.0"')
        print(f'       "{sys.executable}" scripts\\dogrulama\\ttm_dogrula2.py')
        return 2

    print(f"borsapy {getattr(bp, '__version__', '?')} | python {RESULT['python']}")
    check(bp, "THYAO", "industrial")
    check(bp, "AKBNK", "bank")

    fails = sum(1 for c in RESULT["kontroller"] if c["durum"] == "FAIL")
    print(f"\n=== SONUÇ: {fails} hata, {len(RESULT['kontroller'])} kontrol ===")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULT, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Rapor: {OUT}")
    print("Bunu bana gönder — kalem adları ve PD/fiyat alanları içinde.")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
