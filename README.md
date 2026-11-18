# BIST--Man

**KAP bildirimlerini izleyip BIST şirketlerinin mali tablolarıyla oranlayan, kural
tabanlı skorlayan ve Telegram'a önem sırasıyla gönderen hat.**

Tek cümlelik fark: *"haber geldi"* demiyor — **haberin tutarını bilançoya bölüp**
"bu şirket için ne kadar büyük?" sorusunu sayıyla cevaplıyor, sonra eşiğe göre
push / özet / sessiz kararı veriyor.

> Kişisel + eğitim amaçlı. Veri: KAP ve İş Yatırım'ın kamuya açık uçları.
> **Kurulum gerekmez**: pandas/numpy/borsapy yok, her şey standart kütüphane.

---

## Ne yapıyor

```
KAP akışı (3 sn)          triyaj              tutar               oran                 karar
─────────────────  →  ─────────────  →  ───────────────  →  ─────────────  →  ────────────────────
ÖDA/FR/DKB satırı      %74 elenir        özet → gövde        tutar / TTM ciro      push ≥ 6.5 · özet ≥ 3.5
                       (rutin/mekanik)   (1 istek/aday)      (gerçek TTM)          günlük bütçe 3/hisse
```

Örnek (gerçek sayılarla):

| Haber | Şirket | Oran | Karar |
|---|---|---|---|
| 15 milyar TL sözleşme | THYAO (ciro 1,36 trilyon) | %1.1 | **özet** |
| 1 milyar TL sözleşme | MTRKS (ciro 3,2 milyar) | %31.2 | **push 10.00** |
| 15 milyar TL sözleşme | MTRKS | %468 (şüpheli) | **push, etiketli** |
| 500 milyon TL dolar cinsinden | MTRKS | kur yok | sessiz (bilinen kör nokta) |

---

## Hızlı başlangıç

```bash
python3 -m venv .venv
.venv/bin/pip install -q pytest httpx      # sadece test/Telegram için
.venv/bin/pytest                            # 350 test, ağ yok, ~0,5 sn
```

Windows PowerShell karşılığı:

```powershell
python -m venv .venv
.venv\Scripts\pip install -q pytest httpx
.venv\Scripts\pytest
```

Gerçek veri çekmek için (senin makinende, ağ gerektirir):

```powershell
python scripts\kalibrasyon_gercek.py --limit 5     # KAP geçmişi + fiyat + gövde
python scripts\kalibrasyon_gercek.py               # tam koşu (47 hisse)
```

---

## Nerede olduğu (dürüst tablo)

| Faz | İçerik | Durum |
|---|---|---|
| 0 | Saf ayrıştırma, triyaj, config | ✅ |
| 1 | TTM/tablo doğrulaması, stdlib İş Yatırım istemcisi | ✅ |
| 2a | KAP istemcisi + ayrıştırıcılar (gerçek fixture'lar) | ✅ |
| 2b | Unvan→kod eşleme, oran/ceza motoru | ✅ |
| 2c | **Postgres + dedup + cursor + daemon** | ⬜ sırada |
| 3a | Event study (CAR, z, t0) + kalibrasyon ızgarası | ✅ |
| 3b | Kural skor + uçtan uca hat + Telegram istemcisi | ✅ |
| — | Sıralama + günlük bütçe + özet mesajı | ✅ |
| 4 | Gerçek veriyle kalibrasyon (eşiklerin ölçümü) | 🔧 kod ✅ · **koşu sende** |
| 5 | Emtia uyarıları (sigma) | ⬜ |
| 6 | Web arayüzü | ⬜ |
| 7 | LLM (konu/yön sınıfı) | 🔧 sınır hazır, bağlantı yok |

**Bugün ürün değil, ölçülmüş bir karar motoru:** 7/24 çalışan süreç (daemon) ve
DB yok; eşikler hâlâ sezgi (6.5 / 3.5) — kalibrasyon koşusu bunu değiştirecek.

## Sayılarla

| Ölçüm | Değer |
|---|---|
| Test | **350**, ağ/DB/anahtar gerektirmez |
| Gerçek bildirim (fixture) | 89 satır · triyajda **%74** eleniyor |
| TTM hatası (düzeltildi) | naif toplam gerçek TTM'in **1.99×** (THYAO), **2.36×** (AKBNK) |
| Örnek vaka kitabı | 12 vaka: metin → tutar → oran → karar → mesaj |
| KAP maliyeti | 1 istek = 1 yıl geçmiş/hisse · 1 istek = 1 bildirim gövdesi |

## Demolar (hepsi ağsız, hepsi tek komut)

| Komut | Ne gösterir |
|---|---|
| `python scripts/hat_demusu.py` | gerçek KAP akışı → skor → bildirim |
| `python scripts/ornek_vakalar.py` | **12 vaka**: haber + bilanço + karar + Telegram metni |
| `python scripts/gunluk_ozet.py` | sıralama, günlük bütçe, özet metni |
| `python scripts/gercek_veri_ozeti.py` | ne düzeltildi, karar nasıl değişti (gerçek sayılar) |
| `python scripts/haber_davranisi.py` | sisteme göre haber türleri: 10 bulgu |
| `python scripts/onem_denetimi.py` | "önem" ölçeği: çakışmalar, sıra terslemeleri |
| `python scripts/metin_ve_bilanco_denetimi.py` | metinleştirme / sıralama / bilanço kullanımı |
| `python scripts/kalibrasyon_gercek.py --dry-run` | Faz 4 hattı (fixture + sentetik fiyat) |

---

## Mimari

```
                ┌──────────────┐
   KAP ────────►│ sources/kap  │  list/light · detail (gövde) · member_history (1 yıl)
   İş Yatırım ─►│ isyatirim    │  MaliTablo → TTM (kümülatif/tekil, akış/stok)
   Yahoo ──────►│ prices       │  hisse · endeks (İş Yatırım) → event study
                └──────┬───────┘
                       ▼
   triage ──► pipeline ──► scoring ──► digest ──► notify
   (ucuz eleme)  (tutar/oran)  (impact×emniyet)  (sıra+bütçe)  (Telegram, düz metin)
                       │
                       ▼
            analysis/event_study ──► analysis/calibration
            (CAR, z, t0)              (eşik × bütçe ızgarası)
```

| Modül | Sorumluluk |
|---|---|
| `sources/kap.py` | akış, detay, üye geçmişi; retry/backoff, 429 |
| `sources/isyatirim_client.py` | `Data.aspx/MaliTablo` — **yalnız stdlib** |
| `sources/isyatirim_labels.py` | kalem seçimi: itemCode tablo ayrımı, Roma rakamı önceliği |
| `sources/prices.py` | Yahoo (hisse) + İş Yatırım (endeks), disk önbellek |
| `sources/kap_companies.py` | `/tr/bist-sirketler` → `{kod: mkkMemberOid}` |
| `analysis/ttm.py` | kümülatif/tekil tespiti, akış vs stok, doğru TTM |
| `parsing/` | KAP tablosu, gövde (TR-only), tutar, tarih, Türkçe metin |
| `triage.py` | ÖDA/FR/DKB → pipeline \| fundamentals_refresh \| routine \| mechanical |
| `features.py` | oranlar + çarpımsal emniyet cezaları |
| `scoring.py` | `impact = konu + kova`, `skor = impact × emniyet`, gerekçe üretir |
| `pipeline.py` | uçtan uca hat (yan etkiler enjekte) |
| `digest.py` | önem sırası, günlük bütçe, özet metni |
| `notify.py` | Telegram (parse_mode YOK, 4000 karakter bölme, 429) + FakeNotifier |
| `backfill.py` | geçmiş → `ScoredEvent` köprüsü (atıf + event study) |
| `analysis/event_study.py` | t0 seçimi, CAR (endeks arındırılmış), z |
| `analysis/calibration.py` | eşik × bütçe ızgarası → isabet / kaçan |
| `llm.py` | **önerir, karar vermez**: konu/yön/önem + katı JSON şeması |

---

## Dokümanlar

**Durum ve ölçüm**
- [docs/DURUM-RAPORU.md](docs/DURUM-RAPORU.md) — B1–B34: bulgular, ölçümler, riskler
- [docs/GERCEK-VERI-RAPORU.md](docs/GERCEK-VERI-RAPORU.md) — gerçek veriyle ne ölçüldü, ne düzeltildi
- [docs/DURUM-VE-LLM-PLANI.md](docs/DURUM-VE-LLM-PLANI.md) — şu an nasıl + LLM nereye bağlanır

**Denetimler (hepsi kanıtlı)**
- [docs/HABER-DAVRANISI.md](docs/HABER-DAVRANISI.md) — bir habere ne yapıyor: 10 bulgu
- [docs/ONEM-DENETIMI.md](docs/ONEM-DENETIMI.md) — "önem" tek sayıda üç şeyi taşıyor
- [docs/METIN-BILANCO-DENETIMI.md](docs/METIN-BILANCO-DENETIMI.md) — metin sağlam, bilançonun 1/6'sı kullanılıyor

**Ürün**
- [docs/SIRALAMA-VE-DIGEST.md](docs/SIRALAMA-VE-DIGEST.md) — önem sırası, bütçe, özet
- [docs/ORNEK-VAKALAR.md](docs/ORNEK-VAKALAR.md) — 12 vaka, birebir mesajlar
- [docs/FAZ4-KALIBRASYON.md](docs/FAZ4-KALIBRASYON.md) — sende koşulacak tek komut

**Tasarım**
- [docs/PLAN-V1.md](docs/PLAN-V1.md) — evren, kalibrasyon yöntemi, fazlar
- [docs/MIMARI.md](docs/MIMARI.md) — mimari, veri modeli, hata kataloğu
- [docs/TASARIM-DETAY.md](docs/TASARIM-DETAY.md) — skor modeli, state machine, replay
- [docs/VERI-KATMANI-BORSAPY.md](docs/VERI-KATMANI-BORSAPY.md) — veri katmanı + 4 sessiz tuzak
- [docs/VERI-KAYNAKLARI.md](docs/VERI-KAYNAKLARI.md) — kaynak referansı
- [docs/DOGRULAMA-ADIMLARI.md](docs/DOGRULAMA-ADIMLARI.md) — PowerShell doğrulama adımları

---

## Tasarım kuralları

1. **Sezgiyle değil tabloyla**: eşikler ve ağırlıklar ölçümle belirlenir; `config/scoring.toml` ayardır, kod değil.
2. **Sessiz yanlış, gürültülü hatadan kötüdür**: tanınmayan format hata fırlatır, tahmin etmez.
3. **LLM aritmetik yapmaz**: oran/TTM/skor kodda; model yalnız konu/yön/önem önerir, şema dışı alan gelirse yanıt reddedilir.
4. **Eleme sessiz değil**: triyajda düşen her satır sayılır, raporda görünür (`fund_flow`, `bulk_platform`, `foreign`).
5. **Dedup ve kuyruk DB'de** (Postgres `ON CONFLICT`, `FOR UPDATE SKIP LOCKED`) — Redis/Celery yok.
6. **Boş sonuç = şüpheli**, "haber yok" değil → boşluk alarmı.
7. **Gönderim düz metin**: Telegram `MarkdownV2` kaçırma tuzağı (`A.Ş.`, `%3,0`) yok.

## Bilinen sınırlar (açıkça)

| Sınır | Etki | Nerede |
|---|---|---|
| `impact` tek sayıda konu + yön + büyüklük | aynı puan farklı anlam; %100 üstü oranda sıra kırılıyor | `ONEM-DENETIMI.md` |
| Tutarsız maddi haber (konkordato, yangın, işlem yasağı) | `impact 0` → sessiz | `HABER-DAVRANISI.md` F2 |
| Döviz tutarları | kur tablosu yok → `fx_fallback` | F3 |
| Konu önseli kapsamı | 23 satırın 19'unda `varsayılan` | F1 |
| Bilanço | 6 kalemden 1'i (ciro) karara giriyor | `METIN-BILANCO-DENETIMI.md` D |
| Devre kesici sayımı | "3+ uyarı" henüz kodda yok | F7 |
| Kalıcılık | günlük bütçe bellekte; dedup DB'de olacak | Faz 2c |

## Yol haritası

1. **Kalibrasyon koşusu** (senin makinende) → eşikler ölçümden gelsin
2. **Faz 2c**: Postgres + dedup + cursor + daemon → 7/24 çalışma
3. Kural tabanlı **yön + kritik konu** (`force_notify`) → 12 vakanın 4'ü görünür olur
4. FX tablosu (günlük TCMB kuru) → döviz körlüğü
5. Telegram daemon + sabah özeti
6. Emtia uyarıları → web → LLM bağlantısı

## Proje yapısı

```
config/        universe.toml · scoring.toml · scoring.measured.toml · universe_fallback.txt
src/bistman/   parsing · sources · analysis · triage · features · scoring · pipeline · digest · notify · backfill · llm
scripts/       9 demo/ölçüm scripti (hepsi ağsız çalışır, --dry-run / --limit ile)
tests/         350 test · fixtures/kap (gerçek HTML + JSON kesitleri)
docs/          15 rehber + 2 doğrulama sonucu + kalibrasyon çıktısı
```

## Veri kaynakları ve lisans

- KAP kamuya açık uçları (bildirim listesi/detay/üye geçmişi, şirket listesi)
- İş Yatırım `Data.aspx/MaliTablo` ve `ChartData.aspx/IndexHistoricalAll`
- Yahoo Finance chart API (hisse kapanışları)

Kod **kişisel/eğitim** kullanım içindir; veri sağlayıcıların kullanım koşulları
sorumluluğu kullanıcıya aittir. Yatırım tavsiyesi değildir.
