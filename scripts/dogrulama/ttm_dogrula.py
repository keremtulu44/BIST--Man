"""TTM DOĞRULAMA — borsapy'nin TTM'i güvenilir mi? (Windows/PowerShell'de çalışır)

Neden bu script var: hattımızdaki tüm oranların PAYDASI bu sayı. "15 milyar TL
haber / TTM ciro = %3" hesabı, ciro yanlışsa çöp. borsapy'nin TTM'i şöyle
hesaplıyor (kaynak kodundan):

    last_4_quarters = quarterly_df.iloc[:, :4]
    TTM = sum(axis=1)                       # çeyrek sürekliliği kontrolü YOK

Yani: bir çeyrek eksikse 3 çeyreği toplar, farklı para birimi/enflasyon
düzeltmesi karışırsa haberi olmaz. Bu script üç şeyi kontrol eder:

  1. borsapy TTM  ==  bizim kendi 4 çeyrek toplamımız mı?
  2. Son 4 çeyrek gerçekten ARDIŞIK mı (2026Q3, Q2, Q1, 2025Q4)?
  3. Banka (AKBNK) UFRS şablonunda çalışıyor mu, kalem adları ne?

Kendi başına çalışır (repo kurulumu gerekmez):  python ttm_dogrula.py

Çıktı: ekrana rapor + docs/dogrulama-sonuc.json (bana bu dosyayı gönder)
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parents[2] / "docs" / "dogrulama-sonuc.json"

# Gelir tablosunda ciro/kâr satırlarını bulmak için anahtar kelimeler
REVENUE_KEYS = ("satış gelirleri", "satislar", "hasilat", "net satış", "faiz gelirleri")
NET_INCOME_KEYS = ("net dönem kârı", "net dönem karı", "dönem kârı", "ana ortaklık payı")

_RESULT: dict[str, object] = {
    "calisma_zamani_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "python": sys.version.split()[0],
    "kontroller": [],
}


def tr_lower(text: str) -> str:
    table = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})
    return text.translate(table).lower()


def log(status: str, title: str, detail: str = "") -> None:
    icon = {"OK": "[OK]  ", "WARN": "[UYARI]", "FAIL": "[HATA]", "INFO": "[BILGI]"}[status]
    print(f"{icon} {title}")
    if detail:
        for line in str(detail).splitlines():
            print(f"        {line}")
    _RESULT["kontroller"].append({"durum": status, "baslik": title, "detay": str(detail)})


def find_rows(df, keywords: tuple[str, ...]) -> list[str]:
    """Index'te (kalem adı) anahtar kelime geçen satırları döndürür."""
    out: list[str] = []
    for label in df.index:
        name = tr_lower(str(label))
        if any(key in name for key in keywords):
            out.append(str(label))
    return out


def parse_period(col: str) -> tuple[int, int] | None:
    """'2026Q3' / '2026/3' / '2026-09-30' gibi sütun adlarından (yıl, çeyrek)."""
    digits = "".join(ch if ch.isdigit() else " " for ch in str(col)).split()
    if not digits:
        return None
    year = int(digits[0])
    if len(digits) > 1:
        second = int(digits[1])
        if 1 <= second <= 4:
            return year, second
        if 1 <= second <= 12:
            return year, (second - 1) // 3 + 1
    # '2026Q3' gibi harfli biçim
    raw = str(col).upper()
    if "Q" in raw:
        try:
            return int(raw.split("Q")[0][-4:]), int(raw.split("Q")[1][0])
        except (ValueError, IndexError):
            return None
    return None


def consecutive(periods: list[tuple[int, int]]) -> bool:
    for (y1, q1), (y2, q2) in zip(periods, periods[1:]):
        prev_q = q1 - 1 if q1 > 1 else 4
        prev_y = y1 if q1 > 1 else y1 - 1
        if (y2, q2) != (prev_y, prev_q):
            return False
    return True


def check_ticker(bp, ticker: str, financial_group: str | None) -> None:
    print(f"\n=== {ticker} ({'banka / UFRS' if financial_group else 'sanayi / XI_29'}) ===")
    t = bp.Ticker(ticker)
    kwargs = {"financial_group": financial_group} if financial_group else {}

    # 1) Yıllık/toplam gelir tablosu
    try:
        annual = t.get_income_stmt(**kwargs)
        log("OK", f"{ticker} gelir tablosu alındı",
            f"satır={len(annual)}, sütun={list(annual.columns)[:4]}")
    except Exception as exc:
        log("FAIL", f"{ticker} gelir tablosu alınamadı", f"{type(exc).__name__}: {exc}")
        annual = None

    # 2) Çeyreklik tablo — süreklilik kontrolü
    quarterly = None
    try:
        quarterly = t.get_income_stmt(quarterly=True, last_n=8, **kwargs)
        cols = [str(c) for c in quarterly.columns]
        periods = [p for p in (parse_period(c) for c in cols[:4]) if p]
        log("INFO", f"{ticker} çeyreklik sütunlar", ", ".join(cols[:6]))
        if len(periods) == 4 and consecutive(periods):
            log("OK", f"{ticker} son 4 çeyrek ARDIŞIK", str(periods))
        else:
            log("WARN", f"{ticker} çeyrek sürekliliği şüpheli", str(periods))
    except Exception as exc:
        log("FAIL", f"{ticker} çeyreklik tablo alınamadı", f"{type(exc).__name__}: {exc}")

    # 3) Ciro / net kâr satırları
    revenue_row = net_row = None
    if quarterly is not None and len(quarterly) > 0:
        rev_rows = find_rows(quarterly, REVENUE_KEYS)
        net_rows = find_rows(quarterly, NET_INCOME_KEYS)
        log("INFO", f"{ticker} ciro adayı satırlar", "\n".join(rev_rows[:6]) or "(bulunamadı)")
        log("INFO", f"{ticker} kâr adayı satırlar", "\n".join(net_rows[:6]) or "(bulunamadı)")
        revenue_row = rev_rows[0] if rev_rows else None
        net_row = net_rows[0] if net_rows else None
        if revenue_row:
            try:
                vals = quarterly.loc[revenue_row].iloc[:4].tolist()
                log("INFO", f"{ticker} '{revenue_row}' son 4 çeyrek", ", ".join(f"{v:,.0f}" for v in vals))
            except Exception:
                pass

    # 4) KRİTİK: borsapy TTM vs bizim toplamımız
    try:
        if financial_group:
            ttm = t.get_ttm_income_stmt(financial_group=financial_group)
        else:
            ttm = t.ttm_income_stmt
        log("OK", f"{ticker} borsapy TTM alındı", f"satır={len(ttm)}, sütun={list(ttm.columns)}")

        if revenue_row and quarterly is not None:
            ours = float(sum(float(v) for v in quarterly.loc[revenue_row].iloc[:4]))
            theirs = float(ttm.loc[revenue_row].iloc[0])
            diff_pct = abs(ours - theirs) / abs(theirs) * 100 if theirs else float("inf")
            same = diff_pct < 0.01
            log(
                "OK" if same else "FAIL",
                f"{ticker} TTM == 4 çeyrek toplamı? {'EVET' if same else 'HAYIR'}",
                f"borsapy TTM={theirs:,.0f}\n"
                f"bizim toplam={ours:,.0f}\n"
                f"fark=%{diff_pct:.4f}",
            )
            _RESULT.setdefault("ttm", {})[ticker] = {  # type: ignore[union-attr]
                "row": revenue_row,
                "borsapy_ttm": theirs,
                "our_sum": ours,
                "diff_pct": diff_pct,
            }
        if net_row and quarterly is not None:
            try:
                net_ttm = float(ttm.loc[net_row].iloc[0])
                log("INFO", f"{ticker} '{net_row}' TTM", f"{net_ttm:,.0f}")
            except Exception:
                pass
    except Exception as exc:
        log("FAIL", f"{ticker} TTM alınamadı", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}")

    # 5) Fiyat çarpanları (PD / F/K) — oran paydası için
    try:
        info = t.fast_info
        keys = ("last_price", "market_cap", "pe_ratio", "previous_close")
        snapshot = {k: info[k] for k in keys if k in info}
        log("OK", f"{ticker} fast_info", json.dumps(snapshot, ensure_ascii=False, default=str))
        _RESULT.setdefault("market", {})[ticker] = snapshot  # type: ignore[union-attr]
    except Exception as exc:
        log("FAIL", f"{ticker} fast_info alınamadı", f"{type(exc).__name__}: {exc}")


def main() -> int:
    try:
        import borsapy as bp
    except ImportError:
        print("[HATA] borsapy kurulu değil. Şu komutu çalıştır:")
        print("       pip install borsapy==0.11.0")
        return 2

    version = getattr(bp, "__version__", "?")
    print(f"borsapy sürümü: {version}")
    _RESULT["borsapy"] = version

    check_ticker(bp, "THYAO", None)      # sanayi şablonu (XI_29)
    check_ticker(bp, "AKBNK", "UFRS")    # banka şablonu

    fails = sum(1 for c in _RESULT["kontroller"] if c["durum"] == "FAIL")  # type: ignore[union-attr]
    warns = sum(1 for c in _RESULT["kontroller"] if c["durum"] == "WARN")  # type: ignore[union-attr]
    print(f"\n=== SONUÇ: {fails} hata, {warns} uyarı, {len(_RESULT['kontroller'])} kontrol ===")  # type: ignore[arg-type]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(_RESULT, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rapor yazıldı: {OUT_PATH}")
    print("Bu dosyayı bana gönder (içinde sayılar var, sır yok).")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
