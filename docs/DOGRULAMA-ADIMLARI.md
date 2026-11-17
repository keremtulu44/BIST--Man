# Doğrulama Adımları (Windows / PowerShell)

İki şeyi doğrulayacaksın. İkisi de 5'er dakika.

| # | Ne | Neden kritik | Çıktı |
|---|---|---|---|
| **A** | borsapy'nin TTM cirosu doğru mu? | Hattaki **tüm oranların paydası**. Yanlışsa `15M / ciro` hesabı ve üstüne kurulu her şey çöp. | `docs/dogrulama-sonuc.json` |
| **B** | KAP geçmiş tablosunun **gerçek HTML'i** | Backfill parser'ı bu HTML'i ayrıştıracak. Tahminle yazılırsa sessizce yanlış ayrıştırır. | `tests/fixtures/kap/*.html` |

---

## 0. Konsolu hazırla (Türkçe karakterler bozulmasın)

PowerShell'e yapıştır:

```powershell
chcp 65001
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
```

> Bu üç satır olmadan çıktıda `ÅŸ` gibi bozuk karakterler görürsün; kod değil, konsol sorunu.

---

## 1. Repoyu al

```powershell
cd C:\
git clone https://github.com/keremtulu44/BIST--Man.git
cd BIST--Man
git switch arena/2f801e3b-bist-man
```

Zaten klonladıysan:

```powershell
cd C:\BIST--Man
git fetch origin
git switch arena/2f801e3b-bist-man
git pull
```

---

## 2. Python ortamı — ÜÇ YOL VAR

### Yol 0 (en kolay): HİÇBİR ŞEY KURMA

```powershell
cd C:\BIST--Man
python scripts\dogrulama\ttm_dogrula3.py
```

`ttm_dogrula3.py` **sadece Python standart kütüphanesini** kullanır: pandas yok,
httpx yok, borsapy yok, pip yok, venv yok. İş Yatırım'ın JSON ucunu doğrudan
çağırır. Borsapy'nin TTM'ini zaten kullanmıyorduk — paket yalnızca kolaylıktı.

Ağ yoksa mantığı görmek için:

```powershell
python scripts\dogrulama\ttm_dogrula3.py --demo
```

---

## 2b. Python ortamı (borsapy ile) — İKİ YOL VAR

### Yol A (önerilen): tek komut, aktivasyon yok

```powershell
cd C:\BIST--Man
powershell -ExecutionPolicy Bypass -File .\scripts\dogrulama\kur_ve_calistir.ps1
```

Script venv'i kurar, borsapy'yi kurar, doğrulamayı çalıştırır. `Activate.ps1`
ile uğraşmaz: her şey `.venv\Scripts\python.exe` üzerinden doğrudan çağrılır.

### Yol B: elle

`pip install` **yanlış Python'a** gidebilir; `Activate.ps1` yeni pencerede
kendiliğinden aktif olmaz. En güvenlisi, aktivasyonu hiç kullanmamak:
`

```powershell
py --version          # 3.11 veya üstü olmalı
```

Eğer `py` yok veya sürüm eskiyse:

```powershell
winget install --id Python.Python.3.12 -e
```

Sonra (PowerShell'i kapat-aç, `py` tanınsın):

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install borsapy==0.11.0
```

> `Set-ExecutionPolicy` sadece bu PowerShell penceresi için; sistemi kalıcı değiştirmez.

---

Sorun çıkarsa teşhis:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dogrulama\kontrol.ps1
```

Bu, hangi Python'ların kurulu olduğunu ve borsapy'nin **hangi** Python'da
göründüğünü tek tek yazar.

---

## 3. DOĞRULAMA A — TTM

```powershell
python scripts\dogrulama\ttm_dogrula.py | Tee-Object -FilePath docs\dogrulama-cikti.txt
```

Script şunları yazdırır:

- `borsapy sürümü` (0.11.0 olmalı)
- THYAO gelir tablosu: satır sayısı + sütunlar
- **Son 4 çeyrek ARDIŞIK mı?** (2026Q3, Q2, Q1, 2025Q4 gibi)
- Ciro adayı satır adları (borsapy'nin Türkçe kalem adları — bizim eşleme tablomuz için lazım)
- **Kritik satır:** `borsapy TTM == 4 çeyrek toplamı? EVET / HAYIR`
- AKBNK banka şablonu (UFRS) çalışıyor mu
- `fast_info`: PD, F/K

### Ne görmek istiyoruz

| Çıktı | Anlamı |
|---|---|
| `[OK] THYAO TTM == 4 çeyrek toplamı? EVET` | borsapy'nin TTM'i tutarlı → payda olarak kullanılabilir |
| `[FAIL] ... HAYIR` + fark yüzdesi | TTM helper'ı güvenilmez → TTM'i **kendimiz** hesaplarız (4 çeyreği biz toplarız) |
| `[UYARI] çeyrek sürekliliği şüpheli` | Bir çeyrek eksik/karışık → `ttm_gap` cezası devreye girer |
| `[HATA] No financial data available` | Ticker/şablon eşleşmiyor (banka için UFRS şart) |

Hangisi çıkarsa çıksın **işe yarar**: "EVET" ise borsapy'yi kullanırız, "HAYIR" ise kendi TTM'imizi yazarız — ikisi de 30 satırlık iş, ama hangisi olduğunu bilmek şart.

---

## 4. DOĞRULAMA B — KAP fixture'ları

```powershell
.\scripts\dogrulama\kap_fixture_kaydet.ps1
```

Şu üç dosyayı `tests\fixtures\kap\` altına indirir:

| Dosya | Ne işe yarar |
|---|---|
| `member_history_mtrks.html` | **1 yıllık geçmiş tablosu** → backfill parser'ının test verisi |
| `list_light_live.json` | bugünün akışı → ayrıştırıcı gerçek veriyle bir kez daha sınanır |
| `detail_1699030_live.json` | detay ucu → `stockCode`/`relatedStocks` biçimi kesinleşir |

Beklenen çıktı (boyutlar kabaca):

```
member_history_mtrks.html       ~150.000 bayt   OK
list_light_live.json             ~80.000 bayt   OK
detail_1699030_live.json          ~5.000 bayt   OK
```

`SUPHELI (cok kucuk)` derse KAP bot koruması devreye girmiş demektir; 2-3 dakika
bekleyip tekrar çalıştır.

---

## 5. Sonuçları bana getir

**En kolayı:** repoya commit edip push et (senin repon, yetkin var):

```powershell
git add docs\dogrulama-sonuc.json tests\fixtures\kap
git commit -m "dogrulama: ttm raporu + kap fixture'lari"
git push origin arena/2f801e3b-bist-man
```

Push etmek istemezsen: `docs\dogrulama-sonuc.json` içeriğini ve
`tests\fixtures\kap\member_history_mtrks.html` dosyasını (ilk 50 satırı yeter)
sohbete yapıştır.

> `dogrulama-sonuc.json` içinde sadece finansal sayılar var: ciro, kâr, PD, F/K.
> Token, şifre, kişisel veri yok.

---

## Ne olmayacak

- Screenshot gerekmiyor, sayı yeter.
- BIST 50'nin tamamını çalıştırmana gerek yok; **2 hisse** (THYAO sanayi + AKBNK banka) yeter — biri sanayi biri banka şablonunu temsil ediyor.
- Her şey `[HATA]` verse bile sorun değil: o zaman borsapy katmanını **kendi İş Yatırım istemcimizle** değiştiririz (60 satır, `sources/` arkasında olduğu için tek dosya).
