# Ölçüm kaydı: KAP ve mali tablolar (17 Kasım 2026)

> Rakamlar aşağıdaki örneklemlere, faz ve test sayıları o günkü gönderim motoruna aittir. Bu bir canlı tarama veya grafik alarmı performans raporu değildir. [Güncel amaç ve kapsam](ANA-MANTIK.md).

**Tarih:** 17.11.2026 · **Kaynak:** kullanıcının makinesinde alınan gerçek KAP ve
İş Yatırım yanıtları. Aşağıdaki komut başlıca karşılaştırmaları ağsız gösterir; bugünün KAP akışını indirmez.

```powershell
python scripts\gercek_veri_ozeti.py     # bu dokümanın sayılarını basar
```

---

## 1. Tek paragraf

Kayıtlı 9 bildirimli örnekte 6 satır skorlanmadan ayrıldı. Bu bir günlük piyasa geneli eleme oranı değildir. Gerçek veriyle
sınandığında **beş sessiz hata** çıktı ve beşi kapatıldı. En pahalısı: ciro paydası
**2 katı** yanlıştı — sistem yanlış çalışmıyordu, *fazla sessiz* çalışıyordu.

---

## 2. Ölçülen sayılar: ne yanlıştı, ne oldu

| Kalem | İlk hâli (paket ne diyordu) | Gerçek | Fark | Nasıl düzeltildi |
|---|---|---|---|---|
| THYAO TTM ciro | 2.721.671.196.737 | **1.364.849.196.737** | 1.99x | Tablo kümülatif (YTD); `FY + YTD − geçen yıl YTD` |
| AKBNK TTM faiz geliri | 1.602.088.860.000 | **679.718.603.000** | 2.36x | aynı |
| THYAO net kâr | 534.564.032 *(vergi kalemi!)* | **146.839.766.848** | 275x | `itemCode` tablo ayrımı → gelir tablosu |
| THYAO özkaynak | 1.399.181.096.201 *(“TTM”)* | **1.018.453.000.000** | %37 | Stok kalem → son bilanço değeri |
| AKBNK 2025Q3 özkaynak | 0 | **veri yok** (`data_gap`) | — | 0 = boşluk; `tutar/0` imkânsız |

Neden böyle oldu, tek tek:

**Kümülatif tablo.** İş Yatırım'ın çeyreklik gelir tablosu YTD: `2026Q2` = ilk 6 ay,
`2025Q4` = 12 ayın tamamı. Paket son 4 sütunu topluyordu → yıl içi dönemler iki kez
sayılıyor. Kanıt: `yıllık[2025] == 2025Q4` birebir eşit.

**Vergi kalemi.** Uç üç tabloyu birden döndürüyor (bilanço 1xxx/2xxx, gelir 3xxx,
nakit akışı 4xxx); THYAO'da 133 kalem. `Dönem Karı Vergi Yükümlülüğü` (nakit akışı,
0.53 milyar) net kâr sanılmıştı. `Kod` alanıyla tablo ayrımı yapılıyor.
Banka şablonunda nakit akışı tablosu **yok** (AKBNK: 66+64+62).

**Stok ≠ akış.** Özkaynak ve aktif bir dönemde birikmez, bir tarihteki durumdur.
Dört çeyreği toplamak dört fotoğrafı üst üste bindirmek. Stok kalemlerde "son değer".

**Sıfır = boşluk.** AKBNK 2025Q3 özkaynak `258.9 → 0 → 310.2`. Bir çeyrekte sıfırlanıp
geri gelen bilanço yok; 0 eksik sayılıp `data_gap` bayrağı bırakılıyor.

---

## 3. Aynı haber, iki payda — karar nasıl değişiyor

15 milyar TL'lik sözleşme duyurusu, THYAO, `config/scoring.toml` ile:

| Haber | Payda | Oran | impact | push_score | Karar |
|---|---|---|---|---|---|
| Yeni Sözleşme (15 mlr) | yanlış | 0.55% | 2.50 | 2.50 | **silent** |
| Yeni Sözleşme (15 mlr) | **doğru** | 1.10% | 4.50 | 4.50 | **digest** |
| Yatırım Kararı (3 mlr) | yanlış | 0.11% | 2.00 | 2.00 | silent |
| Yatırım Kararı (3 mlr) | doğru | 0.22% | 2.00 | 2.00 | silent |
| Söylenti (tutar yok) | — | — | 1.00 | 0.45 | silent |

Okunuşu: payda iki katı büyükken oran yarıya düşüyor, haber bir bucket aşağı kayıyor
(`kucuk` → `etkisiz`), eşiği geçemiyor. **Aynı haber, aynı eşik, farklı gerçeklik.**
Bu tür hata log'da görünmez: kod çalışır, test geçer, sayı "tutarlı".

---

## 4. Gerçek KAP akışı: 9 bildirim nerede duruyor

```
KAP /list/light (17.11.2026, 15:34)
 │
 ├─ filtered_routine      4   Şirket Genel Bilgi Formu, Genel Kurul İşlemleri,
 │                            Portföy Dağılım Raporu, Pay Dışında SP Aracı
 ├─ filtered_fund         1   INVEO portföy/fon bildirimi
 ├─ filtered_mechanical   1   Pay Bazında Devre Kesici (sayılır, push edilmez)
 └─ scored                3   MTRKS söylenti · GOLDA ÖDA · MARGUN ÖDA
                              hepsi eşik altı → notified = 0
```

**9 kaydın 6’sı skorlanmadan elendi.** Detay gerekirse adayın bildirim numarasıyla istenir; bu tekrar oynatım bir canlı istek sayımı değildir. Gerçek yıllık dağılım da aynı yönde: MTRKS'in 89
bildirimi = 39 DKB + 27 ÖDA + 13 FR + 10 DG; DKB + DG yaklaşık %55’tir. Sınıf dağılımı tek başına “bilgi taşımıyor” demek değildir; konu istisnaları vardır.

Eşiği geçen olmaması hata değil: BIST 50'de rastgele bir salı gününde tetikleyici
haber olmaması normal. "Her gün 3 bildirim" bir hedef değil, üst sınır.

---

## 5. O aşamadaki gönderim hattı (dosya dosya)

```
KAP firehose ──► triage.route() ──► ticker_map ──► isyatirim_client ──► features ──► scoring ──► notify
 (sources/kap)   (sınıf+konu)     (unvan→kod)     (4 dönem/istek)      (3 oran)    (kural)   (Telegram)
                       │               │                  │
                  ön eleme        eşleşme durumu       TTM düzeltmesi
```

| Dosya | Sorumluluk | Gerçek veriyle sınandı |
|---|---|---|
| `sources/kap.py` | akış, detay, üye geçmişi (1 istek = 1 yıl) | ✅ 9 + 89 kayıt |
| `parsing/kap_table.py` | geçmiş tablosu, gövdeyi TR'ye indirme | ✅ 89 satır, 29 test |
| `triage.py` | ÖDA/FR/DKB/DG + konu kalıpları | ✅ 89 kayıt dağılımı |
| `ticker_map.py` | unvan → kod (ASCII katlama) | ✅ TR karakter testleri |
| `sources/isyatirim_client.py` | MaliTablo: **sadece stdlib** | ✅ THYAO + AKBNK |
| `sources/isyatirim_labels.py` | tablo ayrımı + kalem seçimi | ✅ 133/192 kalem |
| `analysis/ttm.py` | kümülatif tespiti, akış/stok | ✅ 8 çeyrek |
| `features.py` | oran üçlüsü + çarpımsal emniyet | ✅ |
| `scoring.py` | `impact × confidence`, eşik, gerekçe | ✅ |
| `notify.py` | Telegram + sahte notifier | ✅ 7 test |

---

## 6. Henüz ölçülmeyenler (dürüst boşluklar)

| Ne | Neden yok | Ne zaman |
|---|---|---|
| **Fiyat / CAR kalibrasyonu** | Fiyat geçmişi çekilmedi | Faz 4 |
| **Eşiklerin doğruluğu** | `config/scoring.toml` sayıları hâlâ sezgiyle | Faz 4 tabloyla doldurur |
| **Piyasa değeri** | `fast_info` boş döndü; PD hesabı için fiyat + pay sayısı gerekli | Faz 4 |
| **44 hisse** | Yalnız 2 hisse (1 sanayi + 1 banka) doğrulandı | Faz 4 backfill |
| **Emtia paneli** | Hiç canlı çalıştırılmadı | Faz 5 |
| **Telegram** | Kullanıcı botu kendi kuracak; istemci hazır, canlı denenmedi | Faz 5 |

---

## 7. Ölçüm tarihindeki durum

```
✅ Faz 0-3b   ayrıştırma → triaj → oran → skor → uçtan uca hat → Telegram istemcisi
✅ Veri       kümülatif düzeltme, tablo ayrımı, stok/akış, stdlib istemci
⏳ Faz 4      gerçek kalibrasyon: backfill + fiyat + event study + eşik tablosu
⏳ Faz 5      emtia paneli, günlük özet (09:15 / 18:15), zamanlama
⏳ Faz 2c     Postgres şeması + dedup/cursor
⏳ Faz 6-7    web arayüzü, LLM (v1'de yok)
```

Rapor yazıldığında 270 test vardı. Bugünkü kurulum için [README](../README.md#hızlı-başlangıç) geçerlidir; İş Yatırım istemcisi standart kütüphaneyi, KAP istemcisi `httpx` kullanır.

O aşamada planlanan sonraki deney, geçmiş KAP satırlarını fiyatla eşleştirip CAR ile eşik/bütçe tablosu oluşturmaktı. Bu, hisse ve alarm zamanı ile bağlam sorgulamanın önkoşulu değildir.
