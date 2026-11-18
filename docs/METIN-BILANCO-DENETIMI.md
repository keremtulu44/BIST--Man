# Ölçüm kaydı: metin, sıralama ve bilanço kullanımı

> Bu inceleme eski gönderim hattında `digest.rank` eklenmeden önce yapıldı. Sonraki durum [sıralama ve digest](SIRALAMA-VE-DIGEST.md) belgesinde; güncel alarm yolu ise [Ana mantık](ANA-MANTIK.md) belgesindedir.

Üç soru tek koşuda: `scripts\metin_ve_bilanco_denetimi.py` (ağ yok, 89 gerçek
bildirim + 11 unvan + 8 para kalıbı + 6 bilanço senaryosu).

**Özet:** Metinleştirme **sağlam** (Türkçe normalizasyon, sayı ayrıştırma doğru
çalışıyor). Ölçüm sırasında **gönderim sıralayıcısı henüz yoktu** (`digest`
yalnız etiketti) ve **bilançonun 6 kaleminden 1'i** karara giriyor.

---

## A · Metin → sayı

| Ölçüm | Sonuç |
|---|---|
| 89 gerçek bildirimde tutar bulunan satır | **0** |
| Bu örnekte tutar aramak için gereken ek kaynak | bildirim **gövdesi** |
| Birden çok tutar içeren satır | 0 |

Bu geçmiş kesitinde başlık ve özetten tutar çıkarılamadı; tutar ölçümü için gövde gerekli (Faz 4
raporundaki "medyan skor 0.00" bulgusunun kökü bu).

### Para kalıpları

| Metin | Sonuç | Doğru mu |
|---|---|---|
| "Sözleşme bedeli 15 milyar TL" | 15.000.000.000 | ✅ |
| "Toplam 1.250.000.000 TL" | 1.250.000.000 | ✅ |
| "Bedel 500 milyon dolar + KDV" | — `fx_fallback` | ⚠️ kur yok → kör |
| "Yatırımın %25'i" | — `no_amount` | ✅ (yüzde, tutar değil) |
| "Hisse başına 2,35 TL" | 2.35 | ✅ (TR ondalık) |
| "Sermaye 250 mn → 500 mn" | 500.000.000 | ❌ artış 250 mn olmalı |
| "3. çeyrekte 4,2 milyar TL kâr" | 4.200.000.000 | ✅ (EN ondalık tuzağı yok) |

Kural "en büyük tutar ana tutardır"; *eski→yeni* ve *şirketin payı* kalıplarında
ters çalışıyor (önceki denetimde F4).

## B · Normalizasyon: sağlam

```
MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.   → matriks finansal teknolojiler
MATRIKS FINANSAL TEKNOLOJI A.S.      → matriks finansal teknoloji
TÜRKİYE İŞ BANKASI A.Ş.             → turkiye is bankasi
TÜRKİYE IS BANKASI A.S.             → turkiye is bankasi      ← aynı anahtar ✅
ŞİŞECAM T.A.Ş.                       → sisecam                 ← çift ek atma ✅
GARANTİ BBVA A.O.                    → garanti bbva
```

```
tr_lower("İŞ BANKASI") = "iş bankası"   tr_lower("IŞIK") = "ışık"
tr_lower("İSTANBUL")  = "istanbul"      tr_lower("ÇĞİÖŞÜ") = "çğiöşü"
```

Türkçe `İ/I/ı` tuzağı, hukuki ek atma, ASCII katlama: hepsi doğru. **Burada
düzeltilecek bir şey yok** — metin normalizasyonu projenin sağlam parçası.

### Gerçek zayıflık: unvanın *nerede* olduğu

Canlı akış (`/api/disclosure/list/light`) ticker vermiyor; atıf tamamen başlık
metnine kalıyor. Ölçüm:

```
tüm satırlar:            çözülen 47/89  ·  çözülemeyen 42
ticker gereken satırlar: çözülen 18/23  ·  çözülemeyen 5
```

Çözülemeyen 42'nin çoğu triyajda zaten düşüyor (devre kesici, tipe dönüşüm →
ticker gerekmiyor). Ticker'ın gerçekten gerektiği 23 satırın 5'i çözülemiyor ve
**hepsi çok sembollü**: fon akışı (9 sembol), KAP piyasa duyurusu (18), SPK
işlem yasağı (77). Bunları tek hisseye yazmak tahmin olurdu; yazmıyoruz.

Bu turda eklenen: **tek sembollü platform başlığı** için başlık-sembolü yedeği
(`BISTECH ... / MTRKS` → MTRKS). Bu fixture'da kazancı 0 (o satırlar filtreleniyor),
ama sınıf gerçek: KAP duyurusu tek hisseyi ilgilendirdiğinde unvan eşleşmesi boş
dönüyor.

**Yapısal not:** triyaj ticker'dan **önce** çalışıyor → filtrelenen satırlar hiç
ticker almıyor. "3+ devre kesici" sayımı yapılacaksa bu sıra değişmeli.

## C · İlk incelemede sıralama eksikti

Aynı 86 sonuç, iki farklı okuma:

```
ÇIKIŞ SIRASI (KAP yayın sırası)          ÖNEM SIRASI (skora göre)
2026-11-17 11:53  skor 0.45             2026-11-17 11:53  skor 0.45
2026-11-05 16:36  skor  —               2026-10-05 06:25  skor 0.00
2026-11-05 16:35  skor  —               2026-09-10 15:11  skor 0.00
2026-11-05 16:00  skor  —               2026-08-26 15:13  skor 0.00
2026-10-30 14:42  skor  —               2026-06-16 15:11  skor 0.00
```

Kod kanıtı:

- `process_batch` sonuçları **giriş sırasıyla** döndürür; içinde `sorted(` yok.
- `sorted(` yalnız `analysis/calibration.py` (backtest) ve etiket eşlemede var.
- `digest` bir **etiket**: `should_notify` yalnız `push`/`push_uncertain` için
  true. Digest'e düşen olayı **toplayan, sıralayan, gönderen kod yok**.
- `max_push_per_ticker_per_day` ayarda var, uygulanmıyor (önceki denetim F8).

Dolayısıyla bu deneyin yapıldığı sürümde **önem sıralaması yoktu**:
gönderim sırası = KAP'ın yayın sırası; digest = hesaplanıp atılan sayı.

## D · Bilanço kullanımı: 6 kalemden 1'i karara giriyor

Aynı haber (500 mn TL, temettü), kalemler tek tek değiştiriliyor:

| Senaryo | Oran | impact | skor | karar |
|---|---|---|---|---|
| temel | %15.62 | 7.5 | 7.50 | push |
| net kâr ×0.1 | %15.62 | 7.5 | 7.50 | push |
| net kâr ×10 | %15.62 | 7.5 | 7.50 | push |
| net kâr **negatif** | %15.62 | 7.5 | 7.50 | push |
| piyasa değeri 5 mlr | %10.00 | 7.5 | 7.50 | push |
| piyasa değeri 800 mn | %62.50 | 9.5 | 9.50 | push |

| Kalem | Rol | Sonuç |
|---|---|---|
| `revenue_ttm` | payda | **KULLANILIYOR** — her karar buna dayanıyor |
| `net_income_ttm` | hesaplanır, `primary_ratio` kullanmaz | ATIL |
| `equity` | parse edilir, `features`'a hiç geçmez | ATIL |
| `total_assets` | `isyatirim_labels` içinde çözülür, modele girmez | ATIL |
| `market_cap` | parametre var, **kaynak yok** → canlı yolda hep None | ÖLÜ KOD |
| `period_end` | taşınır, tazelik kontrolü yok | KONTROLSÜZ |

İki ek kusur:

1. **`statement_type` parametresi hiç kullanılmıyor** (`build_features` alıyor,
   gövdesinde geçmiyor) → banka/sanayi ayrımı skora girmiyor,
   `bank_template_mismatch` cezası hiç tetiklenmiyor.
2. **`stale_fundamentals` yalnız TTM geri-düşüşünde** üretiliyor; 6 ay eski bir
   bilanço bayraklanmıyor. `period_end` var ama kimse "bugün" ile karşılaştırmıyor.

Yani bilanço bugün **tek sayı**: ciroyu payda olarak veren bir bölme tablosu.
Kârlılık, özkaynak, aktif büyüklüğü, PD — dördü de toplanıyor ama kullanılmıyor.

---

## Ölçüm tarihindeki öncelikler

| # | Bulgu | Tür | Emek | Etki |
|---|---|---|---|---|
| 1 | C: sıralama/digest yok | kod + tasarım | orta | yüksek — "önem sırası" ürünün özü |
| 2 | D1: `statement_type` ölü → banka/sanayi ayrımı yok | kod | küçük | orta |
| 3 | D2: tazelik kontrolü yok | kod | küçük | orta (yanlış payda sessiz) |
| 4 | D: net kâr/özkaynak/PD atıl | tasarım + veri (PD kaynağı) | büyük | yüksek (çoklu oran) |
| 5 | A: gövdesiz tutar yok | zaten çözüm var (`body_provider`) | — | — |
| 6 | B: tek sembollü platform başlığı | **bu turda yapıldı** | — | küçük |

Metin normalizasyonunda yapılacak iş kalmadı; sıralama ve bilanço tarafı açık.

## Tekrar üretmek

```bash
.venv/bin/python scripts/metin_ve_bilanco_denetimi.py
```
