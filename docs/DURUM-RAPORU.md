# Durum Raporu — 2026-11-17

> Kapsam: konuşmadan bugüne ne çıktı. Ölçülebilir durum, **ampirik bulgular**
> (kod değil bilgi), tasarım kararları, açık riskler.

---

## 1. Tek paragraf

BIST bildirimlerini izleyip skorlayacak bir hattın **veri katmanı, ayrıştırma
katmanı ve kalibrasyon motoru** çalışır durumda. Sistemin en riskli üç parçası
(tutar ayrıştırma, KAP akışı, "önemli mi?" etiketi) kod + test olarak duruyor ve
hiçbiri ağa ihtiyaç duymadan doğrulanabiliyor. Kalan iş ağırlıklı olarak
**veri toplama + skor formülü + bildirim**; bunlar mevcut parçaların üstüne
oturuyor.

---

## 2. Ölçülebilir durum

| Gösterge | Değer |
|---|---|
| Commit | 10 |
| Takip edilen dosya | 38 |
| Python satırı (src + tests) | 2.474 |
| Test | **248**, tamamı geçiyor, ağ erişimi yok |
| Süre | ~0.24 s |
| Gerçek KAP yanıtı fixture | 2 (`list_light_sample.json`, `detail_1699030.json`) |
| Dış bağımlılık (runtime) | yok (yalnız `httpx`; `borsapy` Faz 1'de) |

Test dağılımı:

```
test_money 21 · test_triage 29 · test_kap_table 29 · test_ttm 15 · test_features 18
test_kap_source 20 · test_scoring 16 · test_borsapy_src 29 · test_event_study 15
test_pipeline_e2e 14 · test_ticker_map 9 · test_dates 8 · test_calibration 8
test_notify 7 · test_config 4 · test_text 4
```

---

## 3. En değerli çıktı: ampirik bulgular

Bunlar kod değil, **doğrulanmış bilgi**. Hepsi 2026-11-17'de canlı/source-code
okumasıyla teyit edildi; projenin geri kalanını bunlar şekillendirdi.

| # | Bulgu | Nasıl doğrulandı | Etkisi |
|---|---|---|---|
| **B1** | KAP'ın resmî dokümante API'si yok; ama **MKK API Portal** var (13.01.2025, ücretsiz, key'li, 12 KAP servisi) | MKK duyurusu + faaliyet raporu | Kişisel kullanımda gerekmedi; ürünleşmede birinci kapı |
| **B2** | `bildirim-sorgu-sonuc?member=OID` **1 yıllık geçmişi tek GET'te** veriyor (MTRKS: 89 bildirim) | Canlı istek | **En büyük riski kapattı**: backfill = 50 istek, `byCriteria` POST'una gerek yok |
| **B3** | `list/light` akışı **ticker vermiyor**, sadece şirket unvanı | Canlı istek | Unvan→kod eşleme zorunlu hale geldi (`ticker_map.py`) |
| **B4** | Detay ucu ticker'ı **yapısal** veriyor (`stockCode`) ama bildirim başına 1 istek | Canlı istek | Detay sadece filtreden geçenler için çağrılır |
| **B5** | Gerçek akışın **%60–75'i gürültü** (devre kesici, MKK kupon/ÖPY, fon bildirimleri); MTRKS'in 1 yıllık 89 kaydında bilgi taşıyan ~%25 | Canlı veri sayımı | Triyaj "lüks" değil, sistemin kendisi |
| **B6** | Tarih **3 farklı formatta** (light `17.11.2026 15:05:53`, detail `2026.11.17 14:53:07`, tablo `05.11.2026`/`Bugün 14:53`); sınıf JSON'da `ODA`, HTML'de `ÖDA` | Canlı + fixture | Tek normalizasyon noktası şart (`dates.py`, `triage.normalize_class`) |
| **B7** | `borsapy` tamamen **senkron**, `news`/`balance_sheet`/`ttm_*` **cached_property**, TTM "son 4 çeyreği topla", `get_disclosures` regex'e dayalı ve **boş DataFrame dönebilir** | Wheel indirilip kaynak kodu okundu | 4 tasarım kuralı: to_thread köprüsü, Ticker ömrü, TTM doğrulama, boş=şüpheli |
| **B8** | Sınıf dağılımı (MTRKS, 1 yıl): **39 DKB · 27 ÖDA · 13 FR · 10 DG**. `DG` (idari form) hiç hesapta yoktu | Gerçek HTML tablo (89 satır) | `DG` → ROUTINE; 89 kaydın **49'u** (DKB+DG) skorlanmadan elenir |
| **B9** | DKB satırlarında `Kod` sütunu **daima boş** (ihraççı BISTECH); hisse `İlgili Hisseler` sütununda. 6 satırda **o da boş** (12-(4) KAP duyurusu, SPK işlem yasağı, dönüşüm) | Gerçek HTML tablo | Atıf sırası: `Kod` → ilgili hisseler → **üye haritası** (`member_oid`); tablo tek başına yetmiyor |
| **B10** | 4 satırda **çok sembol** (`BERA+MTRKS`, `KRGYO+MTRKS`): piyasa geneli duyurular | Gerçek HTML tablo | Kalibrasyonda çift atıf = kirli CAR; atıf politikası açık olmalı |
| **B11** | Bildirim gövdesi düz metin değil **GWT HTML**, her cümle `content-tr` + `content-en` olarak **iki kez** | Gerçek detay yanıtı (22 KB) | İngilizce kopya atılmazsa aynı tutar iki kez görünür; `body_text` %80 küçültüyor |
| **B12** | Tabloda unvan ve konu **tek hücrede** birleşik; tarih bazen `Bugün 14:53` | Gerçek HTML tablo | Unvan ayrıştırılmazsa eşleştirme şişer; göreli tarih enjekte edilebilir `today` ile çözülür |
| **B13** | **borsapy TTM'i 2x fazla sayıyor.** Çeyreklik tablo KÜMÜLATİF (YTD); borsapy 4 sütunu topluyor. THYAO: 2.72 trilyon vs gerçek ~1.36 trilyon | Kullanıcının makinesinde gerçek çalıştırma; 8 çeyrek + yıllık tablo karşılaştırması | Tüm oranların paydası 2x yanlış olacaktı → haber olduğundan **küçük görünür**, sistem susar. `analysis/ttm.py` kümülatifi tespit edip FY+YTD−YTD hesabı yapıyor |
| **B14** | **Satır seçimi tuzağı:** `faiz gelirleri` araması AKBNK'ta `1.7 Diğer Faiz Gelirleri` (alt kalem, 4.3 mlr) seçti; doğrusu `I. FAİZ GELİRLERİ`. Payda ~40x yanlış | Aynı rapor | `sources/borsapy_src.select_row`: Roma rakamlı üst kalem +, `diğer/alt` −, alt-numaralı (1.7) − |
| **B15** | **`fast_info` BOŞ döndü** (`{}`) — hem THYAO hem AKBNK. PD/F/K yok | Aynı rapor | `ratio_mcap` sessizce `None` olacaktı; `no_market_cap` bayrağı var, `dogrulama2` fiyatın nerede olduğunu arıyor |
| **B16** | borsapy TTM'i **kendi içinde tutarlı** (TTM == 4 sütun toplamı, fark %0.0000) ama **yanlış** | Aynı rapor | "Tutarlılık testi geçti" ≠ "sayı doğru". Doğrulama kriteri: yıllık tabloyla karşılaştırma |
| **B17** | **Kümülatif hipotez KESİNLEŞTİ (gerçek veri):** THYAO TTM 1,364,849,196,737 (borsapy 2.72 trilyon = **1.99x**); AKBNK faiz geliri 679,718,603,000 (**2.36x**) | `dogrulama-sonuc3.json`, 8 çeyrek + 4 yıllık tablo | `analysis/ttm.py` üretimde kullanılabilir; payda sorunu kapandı |
| **B18** | `MaliTablo` **üç tabloyu birden** döndürüyor (bilanço 1xxx/2xxx, gelir 3xxx, nakit akışı 4xxx): THYAO 133, AKBNK 192 kalem | Aynı rapor | `itemCode` ile filtre şart; yoksa nakit akışı kalemi gelir tablosu sanılır |
| **B19** | Bu karışıklık somut hataya dönüştü: net kâr olarak `Dönem Karı Vergi Yükümlülüğü` seçildi (0.5 milyar) | Aynı rapor | `_HARD_NEGATIVE` (vergi/yükümlülük/ertelenmiş/dağılım/geçmiş yıl) → kalem **diskalfiye**, ceza yetmiyor |
| **B20** | THYAO sanayi şablonunda kâr satırı `DÖNEM KARI (ZARARI)`; `Dönem Net Kar/Zararı` nakit akışı kalemi | Kalem dökümü (133 satır) | Eşleme tablosu artık gerçek adlarla sabit |

**B13 projenin en pahalı hatasını önledi:** doğrulama olmasaydı sistem "her haber
küçük" diye susacak, kimse nedenini anlamayacaktı — çünkü kod çalışıyor, testler
geçiyor, sayı tutarlı. Sadece yanlış.

**B8 + B9 backfill parser'ını belirledi:** satır şeması artık testle kilitli
(`tests/test_kap_table.py`, 29 test) — KAP sütun eklerse `KapTableError`, sessizce
eksik geçmiş yok.

**B2 tek başına planı değiştirdi:** "6 aylık veri çekilemezse kalibrasyon planı
çöker" varsayımı bitti.

---

## 4. Kod envanteri

| Modül | Ne yapar | Test |
|---|---|---|
| `parsing/money.py` | TR tutar ayrıştırma: `15.000.000.000 TL`, `1,2 milyar dolar`, `₺1.500.000`, `3.5 milyar` | 21 |
| `parsing/dates.py` | 3 KAP formatı + `Bugün/Dün` → aware UTC; tanınmazsa **hata** | 8 |
| `parsing/text.py` | `tr_lower` (İ/I) + `name_norm` (ASCII katlama, hukuki ek) | 4 |
| `triage.py` | ÖDA/FR/DKB → `pipeline`/`fundamentals_refresh`/`routine`/`mechanical` | 20 |
| `ticker_map.py` | unvan→kod: exact + kelime-kapsama fuzzy + belirsizlik/çakışma reddi | 9 |
| `features.py` | 3 oran (ciro/PD/net kâr), çarpımsal confidence, bayraklar | 18 |
| `sources/kap.py` | firehose: light/detail/history, retry+Retry-After, tanıtıcı UA | 17 |
| `sources/base.py` | Protocol'ler: `DisclosureFeed`, `FundamentalsSource`, `UniverseSource` | — |
| `analysis/event_study.py` | `t0` seçimi, endeks arındırılmış CAR, oynaklık normalize z | 15 |
| `analysis/calibration.py` | eşik × günlük bütçe → isabet / kaçan / ort\|CAR\| | 8 |
| `config.py` + `config/universe.toml` | BIST50 kaynağı + 7 enstrümanlı emtia paneli | 4 |

**Doğrulama yöntemi** (neden bu testlere güvenilebilir):
- Tutar/tarih/triyaj testleri **KAP'tan alınmış gerçek metinlerle**.
- Firehose testleri **gerçek yanıt fixture'ı + `httpx.MockTransport`** ile —
  HTTP yolu gerçekten çalışıyor, ağ yok.
- Event study testleri **kontrol edilebilir sentetik serilerle** (bilinen CAR → bilinen z).

---

## 5. Kalibrasyon demosunun sonucu

Sentetik 311 olay / 86 seans, taban oran **%33.8** (|z|≥2 olanların oranı):

```
eşik=3.5  bütçe=3   push/gün=2.14  isabet=41%  ort|CAR|=2.05%  kaçan=29
eşik=5.0  bütçe=3   push/gün=1.23  isabet=47%  ort|CAR|=2.22%  kaçan=55
eşik=6.5  bütçe=3   push/gün=0.47  isabet=55%  ort|CAR|=2.40%  kaçan=83
eşik=7.5  bütçe=3   push/gün=0.22  isabet=53%  ort|CAR|=2.48%  kaçan=95
```

**Okuma:**
- 6.5'te isabet %55 = taban oranın **1.6 katı** → kural ayırt ediyor (sentetik veride).
- Ama aynı satırda **83 material olay kaçıyor**. "İsabet" tek başına karar verdirmez; iki sütun birlikte okunur.
- 7.5'te isabet **artmıyor** (%53), kaçan 95'e çıkıyor → 7.5 anlamsız. Eğri tavanı gösteriyor.

⚠️ **Bu sentetik.** Korelasyonu biz kurduk; edge'i göstermez, **mekanizmayı**
gösterir. Gerçek cevap Faz 4'te. "Hiçbir varyant taban oranı geçemiyor" sonucu
da mümkündür ve değerlidir (1 haftada öğrenilir, 6 ayda değil).

---

## 6. Kilitlenen tasarım kararları

| # | Karar |
|---|---|
| D1 | Python 3.11+, asyncio, httpx |
| D2 | Kuyruk = Postgres `FOR UPDATE SKIP LOCKED` (Redis/Celery yok) |
| D3 | Dedup = DB unique constraint, bellekte değil |
| D4 | Skor 3 eksen: `impact / sentiment / confidence` |
| D5 | Finansal veri: borsapy (İş Yatırım `MaliTablo`) + bizim TTM doğrulaması |
| D6 | Bildirim akışı: kendi firehose'umuz (borsapy'de piyasa geneli feed yok) |
| D15 | Evren: BIST 50 + XU100/XBANK + emtia |
| D17 | Emtia ayrı akış, hisse `impact` ile aynı ölçekte değil |
| D20 | Kapsam: ÖDA + FR; DKB'den yalnız "KAP Duyurusu"; devre kesici = sayaç |
| D21 | Zamanlama: seans içi anında, dışı 09:15 sabah özeti |
| D22 | Emtia paneli: 7 enstrüman |
| D23 | Backfill: `bildirim-sorgu-sonuc?member=OID` (1 yıl / 50 istek) |

Tam liste: `TASARIM-DETAY.md §1`.

---

## 7. Açık riskler

| # | Risk | Etki | Azaltma / durum |
|---|---|---|---|
| R1 | **borsapy TTM = KAP raporu mu?** doğrulanmadı | Oranların paydası yanlışsa tüm skorlar çöp | Elle doğrulama (THYAO/AKBNK) — *senin makinende* |
| R2 | `member_history` HTML tablo ayrıştırıcısı yazılmadı | Backfill kod tarafı eksik | Gerçek HTML fixture'ı gerekiyor (bir kez kaydet) |
| R3 | KAP/MKK rate limit'leri bilinmiyor | Ban/429 | Savunmacı: 15–60 sn poll, `Semaphore(4)`, backoff hazır |
| R4 | KAP şablon değişimi | Sessiz ölüm | Boş/ayrıştırılamaz → hata; boşluk dedektörü şart |
| R5 | Postgres bu ortamda yok | Faz 2c denenmedi | Şema + `FakeRepo` ile yazılacak, DB'de ayrıca doğrulanacak |
| R6 | Sentetik demo ≠ gerçek edge | Yanlış güven | Faz 4 gerçek veri; "edge yok" sonucuna açık ol |

---

## 8. Sıradaki adımlar

| Sıra | İş | Kim |
|---|---|---|
| 1 | Kural skor (`config/scoring.toml`) + uçtan uca dikey dilim testi | ajan (ağsız) |
| 2 | `borsapy_src.py` adaptörü (to_thread + limiter) + TTM doğrulama | ajan yazar, **sen çalıştırırsın** |
| 3 | `member_history` HTML parser (gerçek HTML fixture'ı ile) | ajan + sen (1 kayıt) |
| 4 | Postgres şeması + dedup/cursor + reaper | ajan |
| 5 | Gerçek veriyle kalibrasyon (1 yıl, 50 hisse) → eşik kararı | ajan + sen |
| 6 | Telegram dispatcher + sabah özeti | sen (bot) + ajan (kod) |

---

## 9. Tek cümlelik sonuç

Veri erişiminin **mümkün ve ucuz** olduğu kanıtlandı (50 istek = 1 yıl),
gürültünün **%60-75** olduğu ölçüldü (triyaj merkeze oturdu), ve karar
mekanizması (event study + kalibrasyon ızgarası) **çalışır ve test edilmiş**
durumda — henüz tek eksik, gerçek veriyle ilk kalibrasyon koşusu.
