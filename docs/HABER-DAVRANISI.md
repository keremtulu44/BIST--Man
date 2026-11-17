# Sistem Bir Habere Ne Yapıyor?

Bu dosya **iddia değil ölçüm**: her sayı `python scripts\haber_davranisi.py`
çıktısından (89 gerçek KAP bildirimi + 15 temsili haber + 6 tuzak metin) geliyor.

Özet cevap: **sistem haberi üç kapıdan geçiriyor, ama üç yerde "haber" sandığı
şeyi veri olarak işliyor** — tutar yoksa, konu tanınmıyorsa ve dövizse haber
görünmez oluyor. Bugünkü hâliyle sistem "büyük tutarlı, TL cinsinden, tanıdığı
konulu" haberleri yakalıyor; gerisini sessizce düşürüyor.

---

## 1. Birinci kapı: triyaj (bedava, tutar gerekmez)

89 gerçek MTRKS bildiriminin dağılımı:

| Kapı | Satır | Ne oluyor |
|---|---|---|
| `pipeline` | 23 | tutar + oran + skor hesaplanır |
| `fundamentals_refresh` | 13 | skorlanmaz, "veri yenilendi" sayılır (Finansal Rapor, Faaliyet Raporu, Sorumluluk Beyanı) |
| `mechanical` | 30 | kaydedilir, skorlanmaz — **29'u devre kesici** |
| `routine` | 23 | kaydedilir, skorlanmaz (genel kurul, bilgi formu, tipe dönüşüm) |

Yani gürültünün **%74'ü** daha skor görmeden düşüyor. Doğru taraf: ucuz eleme.
Sorun: düşen %74 içinde de sinyal var (aşağıda F7).

## 2. İkinci kapı: oran — payda her şeyi belirliyor

Aynı haber, iki bilanço (ciro baz alınmış):

| Haber | Tutar | THYAO (ciro 1.364 trl) | MTRKS (ciro 3.2 mlr) |
|---|---|---|---|
| Yeni Sözleşme | 15 mlr | %1 → **digest** 4.50 | %469 → **push_uncertain** 2.50 |
| Yatırım Kararı | 3 mlr | %0 → silent 2.00 | %94 → **push** 10.00 |
| Temettü | 500 mn | %0 → silent 1.00 | %16 → **push** 7.50 |
| Bedelli Sermaye Artırımı | 1 mlr | %0 → silent 0.00 | %31 → **push** 7.00 |
| Vergi Cezası | 250 mn | %0 → silent 0.00 | %8 → silent 2.50 |
| Finansal Rapor (kâr) | 2 mlr | %0 → silent 0.00 | %62 → **push** 8.50 |
| SPK İşlem Yasağı | — | silent 0.00 | silent 0.00 |
| Konkordato Talebi | — | silent 0.00 | silent 0.00 |
| Üretime Ara Verilmesi (yangın) | — | silent 0.00 | silent 0.00 |

**Bu tablo sistemin karakteri:** büyük şirkette 15 milyarlık sözleşme "digest",
küçük şirkette 500 milyonluk temettü "push". Sezgiye aykırı değil — ama tek
başına bu mekanizma, "küçük şirkette her şey büyük" sonucunu verir.

## 3. Üçüncü kapı: karar

```
impact     = clamp(konu_önseli + oran_kovası, 0, 10)
emniyet    = cezaların çarpımı (tutar yok 0.45, payda yok 0.50, …)
push_score = impact × emniyet
≥6.5 push · emniyet<0.40 & impact≥6 push_uncertain · ≥3.5 digest · altı silent
```

---

## Bulgular (kanıtlı)

### F1 · Konu önseli gerçek haberlerin %83'ünde devre dışı

23 pipeline satırının konu eşleşmesi:

```
 19  varsayılan          +0.0     ← hiçbir anahtara uymuyor
  3  pay alım satım      +0.5
  1  haber ve söylenti   +1.0
```

`config/scoring.toml`'daki 16 anahtar gerçek konu başlıklarını kapsamıyor:
"Sermaye Artırımı", "Kar Payı Dağıtımı", "İhraç Tavanı", "Bağımsız Denetim",
"SPK İşlem Yasağı" yok. **Tutar yoksa impact = 0 → kesin sessiz.**

### F2 · Maddi ama tutarsız haberler tamamen görünmez

Konkordato, yangın/üretim duruşu, işlem yasağı, dava, lisans iptali: bunlar
pahalı olaylar ama KAP metninde rakam çoğu zaman yok. Sistem bunları
`impact 0.00 → silent` yapıyor. Bir şirketin üretimi durduğunda bildirim
gelir, sistem susar.

### F3 · Döviz tutarları kör

```
"Sözleşme bedeli 500 milyon dolar..."   → tutar YOK  [fx_fallback]
"Bedeli 45.000.000 Avro'dur."           → tutar YOK  [no_amount]   (düzeltildi: avro eklendi)
```

`fx` parametresi var ama **hiçbir yerden kur gelmiyor** (repoda FX tablosu yok).
Dolayısıyla USD/EUR tutarlı her haber, `fx_fallback` + impact = önsel → sessiz.
KAP'ta döviz bazlı sözleşme/borçlanma çok yaygın: bu, gerçek bir örneklem kaybı.
(`avro` eşanlamlısı eksikti, bu turda eklendi — sonuç yine `fx_fallback`.)

### F4 · Tutar çıkarımı üç kalıpta yanılıyor

| Metin | Sistem ne okuyor | Doğrusu |
|---|---|---|
| "Toplam yatırım 10 mlr, **şirketimizin payı** 2 mlr" | 10 mlr | 2 mlr → 5x şişme |
| "1.250 mn TL **ve** 750 mn TL tutarında iki sözleşme" | 1.250 mn | 2.000 mn → %38 eksik |
| "Sermaye 250 mn'den 500 mn'ye çıkarılacak" | 500 mn | 250 mn artış → 2x şişme |

Kural "en büyük tutar ana tutardır". KAP metinlerinde bu çoğu zaman doğru,
ama tam da *ortaklık/pay* ve *eski→yeni* kalıplarında ters çalışıyor.

### F5 · Genel başlıklı ÖDA sadece büyüklükle skorlanıyor

Gerçek veride 7 satır "Özel Durum Açıklaması (Genel)". Önseli 0, yani karar
tamamen orana kalıyor: 800 milyon TL sipariş → küçük şirkette push 6.50,
büyük şirkette silent. Konu bilgisi (sipariş mi, dava mı, fabrika mı) hiç
okunmuyor — başlık "Genel" olduğu için.

### F6 · Rutin gürültü hâlâ pipeline'da: boşuna gövde maliyeti

Pipeline'a düşen 23 satırın 5'i rutin sayılmalıydı:
"Bağımsız Denetim Kuruluşunun Belirlenmesi" ×3, "İhraç Tavanına İlişkin
Bildirim" ×2. Bunlar gövde indirtir (istek maliyeti), skoru 0 çıkar.

### F7 · Devre kesici sayılmıyor — kod kendi sözünü tutmuyor

`Route.MECHANICAL` docstring'i: *"kaydedilir, skorlanmaz ama SAYILIR — gün
içinde 3+ devre kesici olağandışı harekettir (ayrı uyarı)"*. Koda bakıldığında
sayım yok: 89 satırın **29'u** (%33) devre kesici ve hepsi sessizce düşüyor.
Bir yılda aynı hissede 29 devre kesici, sistemin hiç söylemediği bir olgu.

### F8 · Günlük push bütçesi ayarda var, kodda yok

`max_push_per_ticker_per_day = 3` yalnız `ScoringConfig`'e okunuyor;
`process_batch` içinde uygulanmıyor. Aynı hisse hakkında 10 büyük haber
gelirse 10 push gider. (Kalibrasyon ızgarası bütçeyi modelliyor — yani ölçüm
ile üretim bu noktada ayrışmış.)

### F9 · Yön (polarite) yok

`impact` bir "büyüklük" ama işaret yok: 1 milyar TL **bedelli sermaye artırımı**
(seyreltme, olumsuz) ile 1 milyar TL **sözleşme** (olumlu) aynı ölçekte
sıralanıyor. `subject_prior`'da "bedelli -1.5" gibi negatifler var ama bunlar
sadece impact'i düşürüyor; "kötü haber" diye bir alan, mesajda bir işaret yok.
Sıralama karışıyor: portföy yöneticisi hangisinin risk olduğunu metinden
okumak zorunda.

### F10 · Çok hisseli duyurular canlı yolda tek hisseye yazılıyor

`_resolve_ticker` → `disclosure.tickers[0]`. MKK'nın 77 hisselik işlem yasağı
duyurusu tek hisseye yazılır, diğer 76 hisse olayı görmez. Backfill tarafında
bu turda düzeltildi (`select_rows` + `bulk_platform` sayımı), **canlı hatta
hâlâ açık**.

---

## Öncelik: hangisini önce düzeltmeli

| # | Bulgu | Tür | Emek | Etki |
|---|---|---|---|---|
| 1 | F2 maddi/tutarsız haber körlüğü | tasarım kararı | orta | en yüksek — sistem bugün bunları hiç görmez |
| 2 | F9 yön yok | tasarım kararı | küçük | yüksek — sıralama anlam kazanır |
| 3 | F8 bütçe uygulanmıyor | kod hatası | küçük | orta — spam koruması |
| 4 | F1 önsel kapsamı | ayar (kalibrasyon) | küçük | orta — Faz 4 tablosu dolduracak |
| 5 | F3 FX yok | yeni küçük modül | orta | orta — örneklem kaybı |
| 6 | F7 devre kesici sayımı | kod | küçük | orta — bedava sinyal |
| 7 | F10 çok hisse (canlı hat) | kod | orta | orta |
| 8 | F4 tutar kalıpları | kod + test | orta | düşük-orta |
| 9 | F5 genel başlık | LLM (Faz 7) | büyük | orta |
| 10 | F6 rutin listesi | ayar | küçük | düşük (maliyet) |

**F2 ve F9 kodu değil kararı değiştirir:** "tutarı olmayan maddi haber" ve
"olumsuz haber" için ayrı bir yol gerekiyor (konu listesi + yön etiketi).
Bunlar `scoring.toml`'a sayı eklemekle çözülmez; önce kabul edilmesi gerekir.

## Tekrar üretmek için

```bash
.venv/bin/python scripts/haber_davranisi.py            # üç bölüm
.venv/bin/python scripts/haber_davranisi.py --verbose  # her kararın gerekçesi
```
