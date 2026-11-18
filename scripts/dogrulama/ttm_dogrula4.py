"""TTM DOĞRULAMA 4 — SON ADIM: tablo ayrımı (itemCode) + kesin satır eşlemesi.

Neden 4. script: 3. script gerçek veriyi getirdi ve kümülatifi kanıtladı
(THYAO 1.99x, AKBNK 2.36x fazla sayım). İki yeni sorun çıktı:

  1. `MaliTablo` ucu **üç tabloyu birden** döndürüyor: bilanço (1xxx, 2xxx),
     gelir tablosu (3xxx), nakit akışı (4xxx). THYAO'da 133 kalem var.
     Nakit akışındaki `Dönem Karı Vergi Yükümlülüğü` "net kâr" sanılmış.
     Çözüm: `itemCode` ile filtre (borsapy de böyle yapıyor: 3 = gelir).

  2. Aynı tabloda benzer isimli kalemler var:
     `DÖNEM KARI (ZARARI)` vs `Dönem Kar/Zararının Dağılımı` vs
     `Geçmiş Yıllar Kar/Zararları`. Seçici artık "vergi/karsilik/dagilim/
     gecmis yil" cezası alıyor.

Bu script her rol için seçilen kalemi, itemCode'unu, 8 çeyreği, modu ve doğru
TTM'i yazar. Ayrıca gelir tablosunun TAM kalem listesini (itemCode + ad) döker —
üretim kodundaki eşleme tablosunu bununla donduruyoruz.

Kullanım:  python scripts\\dogrulama\\ttm_dogrula4.py
Çıktı   :  ekran + docs/dogrulama-sonuc4.json
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

BASE = "https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
}
GROUPS = {"industrial": "XI_29", "bank": "UFRS"}
OUT = Path(__file__).resolve().parents[2] / "docs" / "dogrulama-sonuc4.json"

# itemCode ilk hanesi -> tablo (borsapy ile aynı varsayım; bu script DOĞRULAR)
TABLE_PREFIX = {"1": "bilanco-aktif", "2": "bilanco-pasif", "3": "gelir", "4": "nakit akis"}

ROLE_KEYWORDS = {
    "industrial": {
        "revenue": ("satis gelirleri", "hasilat", "net satislar"),
        "net_income": ("donem kari zarari", "net donem kar", "donem net kar", "donem kari"),
        "equity": ("ozkaynaklar", "ana ortakliga ait ozkaynak"),
        "total_assets": ("toplam varliklar", "toplam aktif", "aktif toplam"),
    },
    "bank": {
        "revenue": ("faiz gelirleri",),
        "net_income": ("net donem kar", "donem kari zarari", "donem kari"),
        "equity": ("ozkaynaklar", "ana ortakliga ait ozkaynak"),
        "total_assets": ("toplam varliklar", "toplam aktif", "aktif toplam"),
    },
}
# Rol -> hangi tablodan seçilecek
ROLE_TABLE = {
    "revenue": "gelir",
    "net_income": "gelir",
    "equity": "bilanco-pasif",
    "total_assets": "bilanco-aktif",
}
NEGATIVE = ("diger", "other", "ertelenmis", "matrah", "onceki donem",
            "vergi", "yukumluluk", "karsilik", "dagilim", "gecmis yil", "durdurulan")

_FOLD = str.maketrans({"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                       "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
                       "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u"})

RESULT: dict = {"zaman": date.today().isoformat(), "python": sys.version.split()[0], "kontroller": []}


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).translate(_FOLD).lower()).strip()


def log(status: str, title: str, detail: str = "") -> None:
    icon = {"OK": "[OK]  ", "WARN": "[UYARI]", "FAIL": "[HATA]", "INFO": "[BILGI]"}[status]
    print(f"{icon} {title}")
    for line in str(detail).splitlines():
        if line:
            print(f"        {line}")
    RESULT["kontroller"].append({"durum": status, "baslik": title, "detay": str(detail)})


# ------------------------------------------------------------------ HTTP


def _open(url: str, timeout: int = 30) -> bytes:
    context = ssl.create_default_context()
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS),
                                timeout=timeout, context=context) as response:
        return response.read()


def fetch_chunk(symbol: str, group: str, chunk: list[tuple[int, int]]) -> list[dict]:
    params: dict[str, str] = {"companyCode": symbol.upper(), "exchange": "TRY", "financialGroup": group}
    for i, (year, month) in enumerate(chunk, 1):
        params[f"year{i}"] = str(year)
        params[f"period{i}"] = str(month)
    url = BASE + "?" + urllib.parse.urlencode(params)
    try:
        raw = _open(url)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"bağlantı hatası: {exc.reason}") from exc

    text = raw.decode("utf-8", "replace").lstrip("\ufeff")
    if not text.strip().startswith("{"):
        raise RuntimeError(f"JSON değil: {text[:120]!r}")
    items = json.loads(text).get("value") or []
    if not items:
        raise RuntimeError("boş yanıt")
    return [
        {
            "label": str(item.get("itemDescTr") or item.get("itemDescEng") or ""),
            "code": str(item.get("itemCode") or ""),
            "values": {chunk[i - 1]: item.get(f"value{i}") for i in range(1, len(chunk) + 1)},
        }
        for item in items
    ]


def fetch_periods(symbol: str, group: str, periods: list[tuple[int, int]]) -> list[dict]:
    """Tüm dönemler; 4'lük parçalarla (server sınırı). Kalemler etiketle birleştirilir."""
    merged: dict[str, dict] = {}
    for start in range(0, len(periods), 4):
        for item in fetch_chunk(symbol, group, periods[start:start + 4]):
            entry = merged.setdefault(item["label"], {"label": item["label"], "code": item["code"], "values": {}})
            if not entry["code"]:
                entry["code"] = item["code"]
            entry["values"].update(item["values"])
    return list(merged.values())


# ------------------------------------------------------------------ dönemler


def quarters_back(count: int = 8, today: date | None = None) -> list[tuple[int, int]]:
    today = today or date.today()
    if today.month <= 2:
        year, month = today.year - 1, 9
    elif today.month <= 5:
        year, month = today.year - 1, 12
    elif today.month <= 8:
        year, month = today.year, 3
    elif today.month <= 11:
        year, month = today.year, 6
    else:
        year, month = today.year, 9
    out = []
    for _ in range(count):
        out.append((year, month))
        month -= 3
        if month <= 0:
            month, year = 12, year - 1
    return out


def years_back(count: int = 4, today: date | None = None) -> list[tuple[int, int]]:
    today = today or date.today()
    return [(today.year - 1 - i, 12) for i in range(count)]


# ------------------------------------------------------------------ seçim


def select(items: list[dict], role: str, kind: str) -> tuple[dict | None, list[str]]:
    """Tabloya göre filtrele, sonra puanla. Bulamazsa None (uydurma yok)."""
    table = ROLE_TABLE[role]
    pool = [i for i in items if TABLE_PREFIX.get(i["code"][:1]) == table] or items
    keywords = ROLE_KEYWORDS[kind][role]
    best, best_score, matched = None, 0, []
    for item in pool:
        f = fold(item["label"])
        if not any(k in f for k in keywords):
            continue
        matched.append(item["label"])
        score = 3
        if re.match(r"^[IVX]{1,5}\.\s", item["label"].strip()):
            score += 4
        if re.match(r"^\d+(\.\d+)+", item["label"].strip()):
            score -= 3
        for hint in NEGATIVE:
            if hint in f:
                score -= 2
        score -= item["label"].count("  ")  # girintili = alt kalem
        if score > best_score:
            best, best_score = item, score
    return best, matched[:6]


def series(item: dict, periods: list[tuple[int, int]]) -> list[tuple[int, int, float]]:
    out = []
    for year, month in periods:
        raw = item["values"].get((year, month))
        if raw is None:
            continue
        try:
            out.append((year, month // 3, float(raw)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def detect_mode(points: list[tuple[int, int, float]], annual: dict[int, float]) -> str:
    q4 = {y: v for y, q, v in points if q == 4}
    for year, fy in sorted(annual.items(), reverse=True):
        if year in q4 and abs(q4[year] - fy) <= 0.005 * max(abs(fy), 1):
            return f"KUMULATIF (yillik[{year}] == {year}Q4)"
        four = [v for y, q, v in points if y == year]
        if len(four) == 4 and abs(sum(four) - fy) <= 0.005 * max(abs(fy), 1):
            return "TEKIL (yillik == toplam 4 ceyrek)"
    return "belirsiz"


def correct_ttm(points: list[tuple[int, int, float]], mode: str) -> tuple[float | None, str]:
    if mode.startswith("TEKIL"):
        if len(points) < 4:
            return None, "4 çeyrekten az"
        return sum(v for _, _, v in points[-4:]), "son 4 çeyrek"
    full = sorted({y for y, q, _ in points if q == 4})
    if not full:
        return None, "tam yıl yok"
    fy_year = full[-1]
    fy = next(v for y, q, v in points if y == fy_year and q == 4)
    last_y, last_q, last_v = points[-1]
    if last_q == 4:
        return fy, f"{fy_year}Q4 (tam yıl)"
    prev = next((v for y, q, v in points if y == last_y - 1 and q == last_q), None)
    if prev is None:
        return fy, f"{fy_year}Q4 (geçen yıl aynı dönem yok)"
    return fy + last_v - prev, f"{fy_year}Q4 + {last_y}Q{last_q} - {last_y - 1}Q{last_q}"


# ------------------------------------------------------------------ akış


def run_symbol(symbol: str, kind: str, q_items: list[dict], a_items: list[dict],
               q_periods: list, a_periods: list) -> None:
    print(f"\n=== {symbol} ({'banka / UFRS' if kind == 'bank' else 'sanayi / XI_29'}) ===")

    counts: dict[str, int] = {}
    for item in q_items:
        table = TABLE_PREFIX.get(item["code"][:1], f"bilinmeyen({item['code'][:1]})")
        counts[table] = counts.get(table, 0) + 1
    log("OK", f"{symbol} tablo dağılımı (itemCode ilk hanesi)", json.dumps(counts, ensure_ascii=False))
    RESULT.setdefault("tablolar", {})[symbol] = counts

    income = [[i["code"], i["label"]] for i in q_items if TABLE_PREFIX.get(i["code"][:1]) == "gelir"]
    RESULT.setdefault("gelir_kalemleri", {})[symbol] = income
    log("INFO", f"{symbol} gelir tablosu kalem sayısı", str(len(income)))

    for role in ROLE_KEYWORDS[kind]:
        item, matched = select(q_items, role, kind)
        if not item:
            log("WARN", f"{symbol} {role}: seçilemedi", f"eşleşenler: {matched}")
            continue
        points = series(item, q_periods)
        annual = {y: v for y, _q, v in series(item, a_periods)}
        mode = detect_mode(points, annual)
        value, formula = correct_ttm(points, mode)
        naive = sum(v for _, _, v in points[-4:]) if len(points) >= 4 else None

        log("INFO", f"{symbol} {role} = {item['label'].strip()!r} [{item['code']}]",
            " · ".join(f"{y}Q{q}={v:,.0f}" for y, q, v in points))
        if value:
            log("OK", f"{symbol} {role} TTM",
                f"mod: {mode}\n"
                f"4 sütun toplamı = {naive:,.0f}\n"
                f"DOĞRU TTM       = {value:,.0f}   [{formula}]\n"
                f"oran            = {naive / value:.2f}x" if naive else f"DOĞRU TTM = {value:,.0f}")
        else:
            log("WARN", f"{symbol} {role} TTM hesaplanamadı", f"mod: {mode}, formül: {formula}")

        RESULT.setdefault("secim", {}).setdefault(symbol, {})[role] = {
            "label": item["label"].strip(), "itemCode": item["code"],
            "mod": mode, "naive": naive, "dogru": value, "formul": formula,
            "ceyrekler": [{"donem": f"{y}Q{q}", "deger": v} for y, q, v in points],
        }


def summary_lines() -> list[str]:
    """Sohbete yapıştırılacak kısa özet (git çalışmasa da yeter)."""
    lines = []
    for symbol, roles in RESULT.get("secim", {}).items():
        lines.append(f"{symbol}:")
        for role, data in roles.items():
            dogru = data.get("dogru")
            naive = data.get("naive")
            ratio = f"{naive / dogru:.2f}x" if dogru and naive else "-"
            lines.append(f"  {role:11} {data['label'][:44]:44} [{data['itemCode']:6}] "
                         f"TTM={dogru if dogru is None else round(dogru):>18} oran={ratio}")
    for symbol, counts in RESULT.get("tablolar", {}).items():
        lines.append(f"{symbol} tablolar: {counts}")
    return lines


def main() -> int:
    print(f"Python {RESULT['python']} · kurulum gerekmedi (stdlib) · {date.today().isoformat()}")
    q_periods, a_periods = quarters_back(8), years_back(4)

    for symbol, kind in (("THYAO", "industrial"), ("AKBNK", "bank")):
        try:
            q_items = fetch_periods(symbol, GROUPS[kind], q_periods)
            a_items = fetch_periods(symbol, GROUPS[kind], a_periods)
        except Exception as exc:
            log("FAIL", f"{symbol} veri çekilemedi", f"{type(exc).__name__}: {exc}")
            continue
        run_symbol(symbol, kind, q_items, a_items, q_periods, a_periods)

    fails = sum(1 for c in RESULT["kontroller"] if c["durum"] == "FAIL")
    print(f"\n=== SONUÇ: {fails} hata, {len(RESULT['kontroller'])} kontrol ===")
    print("\n--- SOHBETE YAPIŞTIRILACAK ÖZET ---")
    for line in summary_lines():
        print(line)
    print("--- ÖZET SONU ---")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULT, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRapor: {OUT}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
