# Önem Denetimi — sistem "önem"e aynı anlamı mı veriyor?

Sorulan: *önem gerçekten veriliyor mu, benzer haberler aynı mantığa mı düşüyor,
alakasız şeyler de aynı sıraya mı giriyor?* Cevap ölçümle: `scripts/onem_denetimi.py`
(6 deney, ağ yok).

**Kısa cevap:** Sistem bir "önem" sıralaması yapıyor ama bugünkü `impact` **tek
sayıda üç ayrı şeyi** taşıyor: (1) haberin konusu, (2) **yönü** (kötü haber
negatif önselle aşağı çekiliyor), (3) maddi büyüklüğü. Üçü aynı eksende toplandığı
için **aynı sayı farklı anlamlara geliyor**, ve büyüklük diğer ikisini 3.4× eziyor.

---

## A · Önem nereden geliyor: konu mu, boyut mu?

```
konu önseli    +2.5 (sözleşme/ihale/birleşme) … 0.0 … −1.5 (bedelli/ceza)
boyut kovası    0.0 (etkisiz) … +8.5 (devasa)
```

En güçlü konu ile en güçlü boyut arasında **3.4× fark**. Boyut tek başına 8.5
puan oynatıyor, konu en fazla 2.5. Yani **impact fiilen bir büyüklük
sıralayıcısı**; konu yalnızca aynı kova içinde sıra değiştiriyor.

Ablasyon bunu doğruluyor (13 olay, kararlar):

| Kaldırılan | Kararı değişen |
|---|---|
| konu önseli (hepsi 0) | **4 / 13 (%31)** |
| boyut puanı (hepsi 0) | **8 / 13 (%62)** |

## B · Aynı sayı, farklı anlam

85 farklı (konu, boyut) kombinasyonu **21** farklı impact üretiyor; bunların
**18**'ine birden fazla anlam düşüyor:

```
impact = 6.5  (7 farklı yol)
  sözleşme        + orta      → küçük şirkette %5'lik sözleşme
  ihale           + orta
  birleşme        + orta
  bedelsiz        + buyuk     → %20'lik bedelsiz
  devre kesici    + buyuk
  genel kurul     + buyuk     ← genel kurul! rutin bir konu
  (tanınmayan)    + buyuk     → konusu bilinmeyen, sadece büyük
```

`impact = 8.5` kümesinde daha çarpıcısı var: **"genel kurul + devasa"** = 8.5,
yani `sözleşme + büyük` ile aynı. Rutin bir konu, tutar büyükse en güçlü
sözleşmeyle eşitleniyor. Sebep: önsel 0, boyut 8.5'i tek başına getiriyor.

## C · Sıra terslemeleri (maddiyat vs skor)

Aynı şirket (ciro 3.2 mlr), aynı ayarlar:

| Haber | Tutar | Ciro oranı | impact | skor | karar |
|---|---|---|---|---|---|
| Yeni Sözleşme | 15 mlr | %469 | 10.0 | **2.50** | push_uncertain |
| Yeni Sözleşme | 3 mlr | %94 | 10.0 | **10.00** | push |
| Yatırım Kararı | 800 mn | %25 | 8.5 | 8.50 | push |
| Temettü | 500 mn | %16 | 7.5 | **7.50** | push |
| Yeni Sözleşme | 120 mn | %4 | 6.5 | 6.50 | push |

Maddiyat 29× büyük olan olay, 2.50 ile %16'lık temettünün **altında**. Sebep
`implausible_ratio` cezası (×0.25) — sezgi doğru (bu oran muhtemelen payda
hatası) ama sonuç: **"daha büyük = daha önemsiz" bölgesi** var.

Oran → skor eğrisi (aynı konu):

```
%0.5 → 2.50 silent     %40 → 10.00 push
%2   → 4.50 digest     %90 → 10.00 push
%6   → 6.50 push      %140 →  2.50 push_uncertain
%15  → 9.00 push      %400 →  2.50 push_uncertain
                       %900 →  2.50 push_uncertain
```

%140 ile %900 arasında sistem **ayrım yapmıyor**. Kırılma tek noktada (%100).

## D · Negatif önsel, kötü haberi gizliyor

| Olay | Mevcut | konu=0 | boyut=0 |
|---|---|---|---|
| Vergi Cezası 250 mn | **silent** | digest | silent |
| Bedelli Sermaye 900 mn | **digest** | push | silent |

`ceza = −1.5`, `bedelli = −1.5` "önem"i düşürüyor — yani **kötü haberi
susturuyor**. Oysa istenen muhtemelen tersi: kötü haber de önemli, sadece
işareti farklı. Bu, önceki denetimdeki F9'un sayısal kanıtı.

## E · Payda anlamı şirkete göre değişiyor

Aynı haber (5 milyar TL sözleşme), yalnız payda değişiyor:

| Payda | Oran | Skor | Karar |
|---|---|---|---|
| THYAO (sanayi cirosu) | %0.4 | 2.50 | silent |
| MTRKS (küçük ciro) | %156 | **2.50** | **push_uncertain** |
| AKBNK (banka faiz geliri) | %0.7 | 2.50 | silent |
| holding (konsolide satış) | %0.6 | 2.50 | silent |

Üçü de 2.50 — biri "önemsiz", diğeri "dev ama şüpheli". **Aynı rakam, zıt
anlam.** Ayrıca bankada payda *faiz geliri*, sanayide *satış*: "%5" iki sektörde
aynı şeyi ölçmüyor, skor bunu bilmiyor.

## F · Sıralama sistematik mi?

36 olay (%0.5–%400 oran × 4 konu):

```
Spearman(oran, skor)  tüm aralık : +0.194
Spearman(oran, skor)  oran < %100: +0.892
ters sıralı çift               : 215/576 (%37)
```

%100'ün altında ilişki güçlü ve tutarlı (ρ≈0.89). Bozulma rastgele değil,
**tek bir kırılma noktasından** geliyor. Yani sistem "saçmalıyor" değil;
**kurgusu gereği** büyüklüğü tek ölçüt yapmış, konuyu ve istisnayı üstüne
yapıştırmış.

---

## Yorum: önem üç ayrı soruyu tek sayıya sıkıştırmış

| Soru | Bugün nerede | Sorun |
|---|---|---|
| **Ne kadar büyük?** | `ratio_bucket` → +0..8.5 | doğru ölçü, ama %100'de kırılıyor |
| **Ne kadar önemli?** | `subject_prior` → −1.5..+2.5 | 3.4× zayıf; gerçek başlıkların %83'ünde 0 |
| **İyi mi kötü mü?** | aynı sayının işareti | kötü haberi **aşağı** çekiyor → susturuyor |

Tek sayı olduğu için: (a) aynı sayı farklı anlamlara geliyor, (b) sıralama
karşılaştırılabilir görünüyor ama değil, (c) politika eşiği (6.5) anlamca
heterojen olayları aynı kefeye koyuyor.

## Öneri: iki eksen + ayrı yön

```
materiality  : 0..10   sadece büyüklük (monoton, tavanlı; %100 kırılması
                        "şüphe" bayrağı olur, skoru çökertmez)
severity     : sınıf   {olumlu, olumsuz, nötr} × {kritik, önemli, rutin}
direction    : +1 / −1 / 0   (mesajda işaret: "▲ olumlu", "▼ risk")
```

Karar tek skaler yerine iki boyutlu politika olur:

- `severity=kritik` + **herhangi** materiality → bildir (tutar yoksa da!)
- `severity=önemli` + `materiality ≥ eşik` → push
- `severity=rutin` → yalnız `materiality ≥ yüksek eşik` (genel kurul 8.5 almaz)

Bu, C'deki 29× terslemeyi ve B'deki çakışmayı **yapısal olarak** bitirir;
sayı ayarıyla değil.

## Faz 4 bunu nasıl ölçer

İki ekseni **ayrı ayrı** kalibre etmek gerekir (tek skaleri kalibre etmek
mümkün değil, çünkü karışım):

1. **Materiality geçerliliği:** CAR ortalaması kova arttıkça artıyor mu?
   (etkisiz < kucuk < orta < büyük < devasa). Artmıyorsa kova sınırları yanlış.
2. **Severity geçerliliği:** konu grupları arasında ortalama |CAR| farkı var mı,
   ve **işaret** tutarlı mı (temettü +, bedelli −, ceza −)?
3. **Kırılma noktası:** %100 üstü olaylarda CAR dağılımı, %100 altından farklı mı?
   Farklıysa ceza haklı; aynıysa ceza ölçüm hatası.

## Tekrar üretmek için

```bash
.venv/bin/python scripts/onem_denetimi.py
```
