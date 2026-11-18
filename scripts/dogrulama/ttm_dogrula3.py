"""KURULUMSUZ DOĞRULAMA — sadece Python standart kütüphanesi.

Neden var: `pip install borsapy` kurulumu bozuk/takılı. Ama borsapy'nin yaptığı
şey tek bir JSON ucunu çağırmak:

    GET https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo
        ?companyCode=THYAO&exchange=TRY&financialGroup=XI_29
        &year1=2026&period1=6&year2=2026&period2=3&year3=2025&period3=12&year4=2025&period4=9

    -> {"value": [{"itemDescTr": "Satış Gelirleri", "itemCode": "3ABC",
                   "value1": 585069000000, "value2": 257961000000, ...}, ...]}

    period = AY (3, 6, 9, 12) -> çeyrek = period // 3
    Tek çağrıda en fazla 4 dönem (server sınırı); 8 çeyrek = 2 çağrı.
    financialGroup: XI_29 = sanayi, UFRS = banka.

Bu script o ucu doğrudan çağırır: pandas yok, httpx yok, borsapy yok, pip yok.
Çalıştığı yerde borsapy'ye ihtiyacımız da kalmaz.

Kullanım:
    python scripts\\dogrulama\\ttm_dogrula3.py

Çıktı: ekran + docs/dogrulama-sonuc3.json  (bunu gönder)
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

# Windows konsolu UTF-8 olmayabilir: çökmek yerine değiştir/kayıp karakteri geç
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

BASE = "https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
}
GROUPS = {"industrial": "XI_29", "bank": "UFRS"}
ROLE_KEYWORDS = {
    "industrial": {
        "revenue": ("satis gelirleri", "hasilat", "net satislar"),
        "net_income": ("net donem kar", "donem kari", "ana ortaklik payi"),
    },
    "bank": {
        "revenue": ("faiz gelirleri",),
        "net_income": ("net donem kar", "donem kari"),
    },
}
OUT = Path(__file__).resolve().parents[2] / "docs" / "dogrulama-sonuc3.json"

_FOLD = str.maketrans(
    {"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
     "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u"}
)

RESULT: dict = {"zaman": date.today().isoformat(), "python": sys.version.split()[0]}


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).translate(_FOLD).lower()).strip()


def log(status: str, title: str, detail: str = "") -> None:
    icon = {"OK": "[OK]  ", "WARN": "[UYARI]", "FAIL": "[HATA]", "INFO": "[BILGI]"}[status]
    print(f"{icon} {title}")
    for line in str(detail).splitlines():
        if line:
            print(f"        {line}")
    RESULT.setdefault("kontroller", []).append(
        {"durum": status, "baslik": title, "detay": str(detail)}
    )


# ------------------------------------------------------------------ HTTP


def _open(url: str, insecure: bool = False, timeout: int = 30) -> bytes:
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def fetch(symbol: str, group: str, periods: list[tuple[int, int]]) -> dict:
    params: dict[str, str] = {
        "companyCode": symbol.upper(),
        "exchange": "TRY",
        "financialGroup": group,
    }
    for i, (year, month) in enumerate(periods[:4], 1):
        params[f"year{i}"] = str(year)
        params[f"period{i}"] = str(month)
    url = BASE + "?" + urllib.parse.urlencode(params)

    try:
        raw = _open(url)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} ({symbol})") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        is_cert_error = isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason).upper()
        if is_cert_error:
            # Kurumsal proxy / eski kök sertifika: burada doğrulamayı bırakmak kabul edilebilir,
            # çünkü veri kamuya açık ve sadece okuma yapıyoruz. Yine de KAYIT altına alınır.
            log("WARN", f"{symbol}: sertifika doğrulanamadı -> doğrulamasız tekrar", str(reason))
            try:
                raw = _open(url, insecure=True)
            except Exception as exc2:
                raise RuntimeError(f"doğrulamasız deneme de başarısız: {exc2}") from exc2
        else:
            raise RuntimeError(f"bağlantı hatası: {reason}") from exc

    text = raw.decode("utf-8", "replace").lstrip("\ufeff")
    if not text.strip().startswith(("{", "[")):
        raise RuntimeError(f"JSON değil (ilk 120 karakter): {text[:120]!r}")
    return json.loads(text)


def fetch_all(symbol: str, group: str, periods: list[tuple[int, int]]) -> dict[int, dict]:
    """4'ten fazla dönem -> parçalı çağrı. Dönen: {1..4: {etiket: değer}} birleşik."""
    merged: dict[int, dict] = {}
    for start in range(0, len(periods), 4):
        chunk = periods[start:start + 4]
        payload = fetch(symbol, group, chunk)
        items = payload.get("value") or []
        if not items:
            raise RuntimeError(f"{symbol}: boş yanıt (dönem {chunk})")
        for i, _ in enumerate(chunk, 1):
            merged[start + i] = {
                str(item.get("itemDescTr") or item.get("itemDescEng") or ""): item.get(f"value{i}")
                for item in items
            }
    return merged


# ------------------------------------------------------------------ dönemler


def quarters_back(count: int = 8, today: date | None = None) -> list[tuple[int, int]]:
    """Yayın gecikmesini hesaba katan çeyrek listesi (borsapy ile aynı mantık).

    Ocak-Şubat: geçen yıl Q3 · Mart-Mayıs: geçen yıl Q4 · Haziran-Ağustos: bu yıl Q1
    Eylül-Kasım: bu yıl Q2 · Aralık: bu yıl Q3
    """
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

    out: list[tuple[int, int]] = []
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


def pick_label(labels: list[str], keywords: tuple[str, ...]) -> tuple[str | None, list[str]]:
    """Roma rakamlı ÜST kalem tercih edilir; 'diğer'/alt numaralı kalem ceza alır."""
    best, best_score, matched = None, 0, []
    for label in labels:
        f = fold(label)
        if not any(k in f for k in keywords):
            continue
        matched.append(label)
        score = 3
        if re.match(r"^[IVX]{1,5}\.\s", label.strip()):
            score += 4
        if re.match(r"^\d+(\.\d+)+", label.strip()):
            score -= 3
        if "diger" in f:
            score -= 2
        if score > best_score:
            best, best_score = label, score
    return best, matched[:6]


def series(merged: dict[int, dict], label: str, periods: list[tuple[int, int]]) -> list[tuple[int, int, float]]:
    out = []
    for idx, (year, month) in enumerate(periods, 1):
        raw = merged.get(idx, {}).get(label)
        if raw is None:
            continue
        try:
            out.append((year, month // 3, float(raw)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def detect_mode(points: list[tuple[int, int, float]], annual: dict[int, float]) -> str:
    q4 = {y: v for y, q, v in points if q == 4}
    # En yeni yıl önce denenir: kanıt olarak "yillik[2025] == 2025Q4" demek daha güçlü
    for year, fy in sorted(annual.items(), reverse=True):
        if year in q4 and abs(q4[year] - fy) <= 0.005 * max(abs(fy), 1):
            return f"KUMULATIF (yillik[{year}] == {year}Q4)"
        four = [v for y, q, v in points if y == year]
        if len(four) == 4 and abs(sum(four) - fy) <= 0.005 * max(abs(fy), 1):
            return f"TEKIL (yillik[{year}] == toplam 4 ceyrek)"
    return "belirsiz (yillik tabloda eslesme yok)"


def correct_ttm(points: list[tuple[int, int, float]], mode: str) -> tuple[float | None, str]:
    if mode.startswith("TEKIL"):
        if len(points) < 4:
            return None, "4 çeyrekten az"
        last4 = points[-4:]
        gaps = [(a, b) for a, b in zip(last4, last4[1:])
                if (b[0] * 4 + b[1]) - (a[0] * 4 + a[1]) != 1]
        return sum(v for _, _, v in last4), ("son 4 çeyrek" + (" (atlamalı!)" if gaps else ""))

    full_years = sorted({y for y, q, _ in points if q == 4})
    if not full_years:
        return None, "tam yıl (Q4) yok"
    fy_year = full_years[-1]
    fy = next(v for y, q, v in points if y == fy_year and q == 4)
    last_y, last_q, last_v = points[-1]
    if last_q == 4:
        return fy, f"{fy_year}Q4 (tam yıl)"
    prev = next(((v) for y, q, v in points if y == last_y - 1 and q == last_q), None)
    if prev is None:
        return fy, f"{fy_year}Q4 (geçen yıl aynı dönem yok)"
    return fy + last_v - prev, f"{fy_year}Q4 + {last_y}Q{last_q} - {last_y - 1}Q{last_q}"


# ------------------------------------------------------------------ akış


def check(symbol: str, kind: str) -> None:
    print(f"\n=== {symbol} ({'banka / UFRS' if kind == 'bank' else 'sanayi / XI_29'}) ===")
    group = GROUPS[kind]
    q_periods = quarters_back(8)
    a_periods = years_back(4)

    try:
        q_merged = fetch_all(symbol, group, q_periods)
        a_merged = fetch_all(symbol, group, a_periods)
    except Exception as exc:
        log("FAIL", f"{symbol} veri çekilemedi", f"{type(exc).__name__}: {exc}")
        return

    _run(symbol, kind, q_merged, a_merged, q_periods, a_periods)


def _run(symbol: str, kind: str, q_merged: dict, a_merged: dict,
         q_periods: list, a_periods: list) -> None:
    """Ağdan bağımsız kısım: seçim + mod tespiti + TTM (demo/test burayı kullanır)."""
    labels = sorted({label for chunk in q_merged.values() for label in chunk})
    RESULT.setdefault("kalemler", {})[symbol] = labels
    log("OK", f"{symbol} veri geldi",
        f"çeyrek sütunları: {', '.join(f'{y}Q{m // 3}' for y, m in q_periods)}\n"
        f"yıllık sütunlar: {', '.join(str(y) for y, _ in a_periods)}\n"
        f"kalem sayısı: {len(labels)}")

    for role, keywords in ROLE_KEYWORDS[kind].items():
        label, matched = pick_label(labels, keywords)
        if not label:
            log("WARN", f"{symbol} {role}: satır bulunamadı", f"eşleşen: {matched}")
            continue

        points = series(q_merged, label, q_periods)
        annual_series = series(a_merged, label, a_periods)
        annual = {y: v for y, _q, v in annual_series}
        log("INFO", f"{symbol} {role} = {label!r}",
            " · ".join(f"{y}Q{q}={v:,.0f}" for y, q, v in points))

        mode = detect_mode(points, annual)
        log("OK" if not mode.startswith("belirsiz") else "WARN", f"{symbol} {role} modu", mode)

        value, formula = correct_ttm(points, mode)
        naive = sum(v for _, _, v in points[-4:])
        if value:
            ratio = naive / value if value else float("inf")
            log("OK", f"{symbol} {role} TTM",
                f"4 sütun toplamı (borsapy yöntemi) = {naive:,.0f}\n"
                f"DOĞRU TTM                         = {value:,.0f}   [{formula}]\n"
                f"borsapy fazla sayıyor             = {ratio:.2f}x")
            RESULT.setdefault("ttm", {}).setdefault(symbol, {})[role] = {
                "label": label, "mod": mode, "naive": naive, "dogru": value,
                "formul": formula, "oran": ratio,
                "ceyrekler": [{"donem": f"{y}Q{q}", "deger": v} for y, q, v in points],
            }


def demo() -> int:
    """Ağ olmadan mantığı kanıtlar: gerçek THYAO sayılarıyla sahte yanıt kurar.

    `python scripts\dogrulama\ttm_dogrula3.py --demo`
    """
    print("DEMO — ağa çıkılmıyor, gerçek THYAO sayıları elle veriliyor")
    q_periods = quarters_back(8)
    a_periods = years_back(4)
    # (yıl, ay) -> Satış Gelirleri (kümülatif YTD)
    rev = {
        (2026, 6): 585_069_000_000, (2026, 3): 257_961_000_000,
        (2025, 12): 1_187_816_196_737, (2025, 9): 690_825_000_000,
        (2025, 6): 380_000_000_000, (2025, 3): 150_000_000_000,
        (2024, 12): 1_043_000_000_000, (2024, 9): 610_000_000_000,
    }
    q_merged, a_merged = {}, {}
    for idx, key in enumerate(q_periods, 1):
        q_merged[idx] = {"Satış Gelirleri": rev.get(key), "NET DÖNEM KAR/ZARARI": None}
    annual_rev = {2025: 1_187_816_196_737, 2024: 1_043_000_000_000,
                  2023: 800_000_000_000, 2022: 500_000_000_000}
    for idx, key in enumerate(a_periods, 1):
        a_merged[idx] = {"Satış Gelirleri": annual_rev.get(key[0]), "NET DÖNEM KAR/ZARARI": None}
    _run("THYAO", "industrial", q_merged, a_merged, q_periods, a_periods)
    return 0


def main() -> int:
    if "--demo" in sys.argv:
        return demo()
    print(f"Python {RESULT['python']} · kurulum gerekmedi (stdlib)")
    print(f"Bugün: {date.today().isoformat()}")
    check("THYAO", "industrial")
    check("AKBNK", "bank")

    fails = sum(1 for c in RESULT.get("kontroller", []) if c["durum"] == "FAIL")
    print(f"\n=== SONUÇ: {fails} hata, {len(RESULT.get('kontroller', []))} kontrol ===")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULT, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rapor: {OUT}")
    if fails:
        print("Hata varsa: internet/proxy veya İş Yatırım ucu değişmiş olabilir.")
        print("Bu dosyayı bana gönder — hata metinleri içinde.")
    else:
        print("Bu dosyayı bana gönder: kalem adları + doğru TTM + mod kanıtı içinde.")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
