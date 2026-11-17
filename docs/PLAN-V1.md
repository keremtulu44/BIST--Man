# PLAN v1 — BIST 50 + endeks + emtia, kural tabanlı, veriyle kalibre

> Cevaplara göre: evren **BIST 50** (+ XU100/XBANK seviyesi) + **altın/gümüş/petrol**;
> eşikler **6 aylık toplu veriyle** belirlenecek; **LLM yok** (Faz 3'te Gemini
> veya kendi wrapper); kanal **Telegram → sonra web**; hedef **ileride ürüne
> dönüşebilir** (mimari hazır, kod tek-kullanıcı).

---

## 1. Evren — borsapy'de doğrulanmış semboller

| Varlık | borsapy çağrısı | Not |
|---|---|---|
| BIST 50 hisseleri | `Ticker("THYAO")` vb. | 50 sembol `config/universe.toml` |
| XU100 / XU050 / XBANK seviyesi | `Index("XU100")`, `Index("XU050")`, `Index("XBANK")` | **piyasa referansı**, ayrı sinyal değil |
| Gram altın (TL) | `FX("gram-altin")` | canlidoviz |
| Ons altın | `FX("ons-altin")` (TL) · TradingView ile USD | |
| Gümüş | `FX("gram-gumus")`, `FX("XAG-USD")` | |
| Brent petrol | `FX("BRENT")` | **USD fiyatlı** — TL'ye çevirirken USD/TRY gerekir |
| Platin / paladyum | `FX("gram-platin")`, `FX("XPT-USD")`, `FX("XPD-USD")` | bonus |
| Geçmiş | `FX(...).history(period="6mo", interval="1d")` | 6 ay günlük ✅ doğrulandı |

**Emtia mimaride nereye oturuyor — iki ayrı şey, karıştırma:**

1. **Ayrı uyarı akışı (v1):** emtianın "bildirimi" olmaz. Kurallı eşik:
   günlük % değişim + 20 günlük volatilite bandı (`|Δ| > 2σ`), TL ve USD bazında.
   Bu ucuz ve hemen işe yarar.
2. **Hisse skorunda bağlam (v2):** KOZAL←altın, PETKM←Brent, THYAO←jet yakıtı,
   FROTO/TOASO←çelik. Bu, "haber + emtia + sektör betası" işi; v1'e koymuyoruz,
   çünkü kural motorunu şişirir.

⛔ **Yapılmayacak:** emtia hareketini hisse `impact` skoruyla **aynı 0-10 ölçeğe**
koymak. "Altın %2 düştü" ile "şirket 5 milyar TL sözleşme aldı" aynı eksende
anlamsız. Ayrı tablo, ayrı eşik, ayrı kanal.

---

## 2. "6 aylık veriyle karar verelim" — evet, ama yöntem şu olmalı

Toplu veri tek başına karar verdirmez: **6 aylık bildirim metni = etiketsiz yığın.**
Etiketi biz üretmek zorundayız. Üretebileceğimiz tek objektif etiket: **fiyat tepkisi.**

### 2.1 Etiket: anormal getiri (event study)

Her bildirim için:

```
t0  = yayın saatinden SONRAKİ ilk seansın açılışı
     (seans içi yayın → ertesi gün; seans öncesi → aynı gün; kapanış sonrası → ertesi gün)
CAR(k) = [kapanış(t0+k) / kapanış(t0-1) − 1] − [XU100 aynı pencerede − 1]
z(k)   = CAR(k) / (σ_günlük × √(k+1))          σ = hissenin son 60 günlük günlük oynaklığı
```

- `k ∈ {0, 1, 3, 5}` → 4 etiket sürümü.
- **Neden normalize:** normalize etmezsen yüksek betalı/oynak küçük hisseler
  sürekli "önemli" görünür. `z`, "bu hisse için normalin kaç katı" sorusunu sorar.
- **Neden endeks çıkarılıyor:** yoksa "her şey yükseldiği gün her haber olumlu"
  saçmalığı çıkar.

**Uyarı (dürüst olalım):** bu etiket "önem" değil **"piyasa etkisi"** ölçer.
Haber zaten fiyatlanmış olabilir; bazı büyük haberler fiyata yansımaz.
Bizim ürünümüz "erken uyarı" olduğu için doğru etiket yine de bu — ama
sonuçları yorumlarken bunu bilelim.

### 2.2 Karar kriteri: "günlük bütçede isabet oranı"

İstatistiksel anlamlılık **kullanmayacağız** (3.000 olayda %5'i zaten şans eseri
"anlamlı" çıkar). Onun yerine ürün sorusuna çeviriyoruz:

> **"Günde en fazla 3 bildirim göndereceksem, bunların kaçı |z| ≥ 2 çıkıyor?"**

Kural motorunun 4-5 varyantını (farklı oran eşikleri, farklı `subject` ağırlıkları)
6 aylık veride çalıştır, şu tabloyu doldur:

| Kural varyantı | push/gün | isabet (|z|≥2) | ortalama |CAR| | kaçırılan devasa olay |
|---|---|---|---|---|
| V1: sadece oran > %3 | 2.1 | %38 | %1.9 | 4 |
| V2: oran + subject prior | 3.0 | %47 | %2.4 | 2 |
| V3: + emtia bağlamı | 3.2 | %44 | %2.3 | 2 |

Kazanan **V2 gibi** bir şey çıkacak büyük ihtimalle. Eşikler buradan gelir,
sezgiyle değil — ve `config/scoring.toml`'a yazılır, koda gömülmez.

### 2.3 Bu deneyin asıl değeri: **fizibilite testi**

Eğer hiçbir varyant şans seviyesinin (%50'ye yakın) üstüne çıkamıyorsa,
**fikirde edge yok.** Bunu 1 haftada öğrenmek, 6 ayda öğrenmekten iyi.
Bu yüzden bu deney "kalibrasyon" değil, **"devam/iptal kararı"**.

---

## 3. EN BÜYÜK BİLİNMEYEN: 6 aylık bildirim geçmişini çekebilir miyiz?

Bunu planın en başına koyuyorum çünkü **cevap "hayır" ise bütün §2 çöker.**

| Yol | Ne verir | Risk |
|---|---|---|
| KAP `list/light` | **sadece bugün** | 6 ay geriye gidilemez |
| KAP `byCriteria` (tarih aralığı, POST) | 7 günlük pencereler → 26 istek | **bot koruması (HTTP 666) tarih sorgusunda görülmüş** |
| `Ticker.news` | hisse başına **son 20** | 50 hisse × 20 = 1.000 kayıt; 6 ayın tamamı değil |
| borsapy `get_disclosure_content(idx)` | tek bildirim gövdesi | ID elimizde olmalı |

**Karar: önce bunu test et.** Sıra:
1. `byCriteria` ile 1 haftalık pencere (Referer + kendi UA + önce `bildirim-sorgu`
   ısıtması). Çalışırsa 26 pencereye çıkar, aralarda 2-3 sn bekle.
2. Engellenirse: 3 aya düş (13 pencere) → hâlâ engel → `Ticker.news` ile 50 hisse
   (son 20'şer) → o da yetmezse **kararı 6 ay yerine "son 1 ay tam + sürekli canlı
   biriktirme"** modeline çevir (sistem çalıştıkça veri birikir, 3 ay sonra karar veririz).

> Bu, projenin **en ucuz ve en kritik** deneyi: 1 script, ~30 dakika. Yanıtı
> bilmeden Faz 2'ye geçmek riskli.

---

## 4. Skor v1 (LLM'siz) — kesinleşen formüller

```
impact_rule = w_subject[subject] + w_ratio[bucket(ratio_revenue)]
              + w_ratio_mcap[bucket(ratio_mcap)]
              + (0 | -1) ileri sürülen bayraklar

confidence  = 1.0 × ∏ cezalar
              (no_amount 0.45, fx_fallback 0.70, ttm_gap 0.60,
               mixed_restatement_vintage 0.60, bank_template_mismatch 0.50,
               implausible_ratio 0.25, stale_fundamentals 0.85)

push_score  = impact × confidence        → eşik §2.2'den
```

- Ağırlıklar (`w_*`) `config/scoring.toml`'da. Kodda sayı yok.
- Rutin `subject` + tutar yok → `filtered` (LLM'e/özet sayacına bile girmez).
- Aynı hisse için günlük anında-push tavanı: **3** (ilk sürüm), özet 18:15.
- Gerekçe metni: kural motoru **kendi cümlesini** üretir ("Ciro'nun %3.2'si,
  sektör ortalamasının üstünde, emin: 0.82"). LLM'siz açıklanabilirlik.

---

## 5. Ürüne dönüşebilirlik: iki uyarı

1. **Veri lisansı duvarı.** borsapy "kişisel/eğitim" lisanslı. Ürünleşme anı
   gelirse veri katmanı değişmek zorunda (istfin / MKK / DataStore / kurumsal).
   Mimari hazır (`sources/`), ama **iş modeli bunu baştan hesaba katmalı** —
   "aylık 50 TL'lik veri + lisans" gerçeği ürün fiyatını belirler.
2. **Şema hazırlığı (kod değil).** `user`, `delivery_pref(user, channel, target,
   threshold, quiet_hours)`, `notification` tablolarını v1'de **tek satırlık
   varsayılan kullanıcıyla** kuralım. Sonradan eklemek migration + veri taşıma;
   baştan koymak bedava.

---

## 6. Faz planı (v1)

| Faz | İçerik | Bitiş kriteri | Süre |
|---|---|---|---|
| **0** ✅ | `money.py` + `features.py` TTM doğrulama + testler | 40+ örnek, ağ yok | — |
| **1** | `borsapy_src.py` (to_thread köprüsü, limiter) + 5 hisse fixture + **elle doğrulama** | THYAO/AKBNK TTM = KAP'taki rapor | 1-2 gün |
| **2 (kritik)** | **6 aylık bildirim backfill deneyi** (§3) | 26 pencere çekildi VEYA fallback kararı | 0.5 gün |
| **3** | `kap_firehose` + `disclosure`/`disclosure_ticker` + dedup + reaper | 24 saat kesintisiz, 0 duplike | 2-3 gün |
| **4** | Fiyat geçmişi (XU100 + 50 hisse) + **event study** (§2) | Varyant tablosu dolu, karar verilmiş | 1-2 gün |
| **5** | Kural skor + Telegram + 18:15 özeti + emtia alarmı | Uçtan uca, LLM maliyeti 0 | 2-3 gün |
| **6** | Web arayüzü (FastAPI, aynı DB) | Bildirim listesi + filtre | 2 gün |
| **7** | LLM (Gemini / kendi wrapper) + replay + golden set | Şema valid %95, replay raporu | 3-5 gün |

**Faz 2'yi atlarsak** 6 ay veri toplama hayaliyle 2 hafta kod yazıp
"veri yok" duvarına çarpabiliriz. O yüzden Faz 2, Faz 3'ten **önce**.

---

## 7. Açık bilinmeyenler (önem sırasına göre)

| # | Bilinmeyen | Neden kritik | Nasıl çözülür |
|---|---|---|---|
| 1 | 6 aylık bildirim geçmişi çekilebiliyor mu | Tüm kalibrasyon planı buna bağlı | Faz 2, 30 dk |
| 2 | `byCriteria` yanıt şeması (alan adları) | Parser'ın temeli | Aynı deney, ham yanıt fixture'a |
| 3 | BIST 50 üye listesi nereden güncel gelir | Evrenin tanımı | `Index("XU050")` bileşen veriyor mu? Yoksa config + çeyreklik elle |
| 4 | borsapy TTM = KAP raporu mu | Tüm oranlar buna bağlı | Faz 1 elle doğrulama |
| 5 | Fiyat verisi 6 ay geriye sağlıklı mı (TradingView) | Event study girdisi | Faz 4, 50 hisse × 6 ay çekip boşluk say |
| 6 | KAP yayın saati hassasiyeti (saniye) | t0 seçimi / event study | Fixture'da `publishDate` formatı: `YYYY.MM.DD HH:MM:SS` |
