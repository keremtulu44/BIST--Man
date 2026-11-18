# Önceki gönderim motoru: 12 örnek vaka

> Bunlar gönderim motorunun ilk sürümündeki senaryolardır, [hisse/zaman sorgusunun](ALARM-BAGLAMI.md) döndürdüğü gelişmeler değildir. Güncel tekrar için `python scripts/ornek_vakalar.py`: konkordato, tek-paya işlem yasağı ve kuru olmayan imzalı sözleşme artık özete girer. [Düzeltmeler ve gönderim onayı](GONDERIM-AKISI.md).

`python scripts\ornek_vakalar.py` **sentetik bildirimleri** kayıtlı (THYAO/AKBNK) ve varsayımsal (MTRKS) mali değerlerle işler. Bu senaryolarda tutarlar gövdededir. Aşağıdaki mesajlar eski örnek çıktılardır; gerçek Telegram teslimi değildir.

Senaryolardaki mali girdiler (2026/6, [örnek scriptteki sabitler](../scripts/ornek_vakalar.py)). Kaynak karşılaştırması için [mali tablo ölçümüne](GERCEK-VERI-RAPORU.md) bak: örneğin senaryodaki THYAO net kâr sabiti o rapordaki net kâr ölçümüyle aynı değildir ve burada sözleşme oranının paydası değildir.

| Hisse | Ciro / faiz geliri (TTM) | Net kâr (TTM) | Özkaynak | Not |
|---|---|---|---|---|
| THYAO | 1.364.849.196.737 | 140.670.766.848 | 1.018.453.000.000 | sanayi |
| AKBNK | 679.718.603.000 *(faiz geliri)* | 66.774.061.000 | 324.746.000.000 | banka |
| MTRKS | 3.200.000.000 | 400.000.000 | 1.100.000.000 | **varsayım** (küçük ölçek) |

Bunlardan karara giren tek kalem **ciro**. Net kâr, özkaynak, toplam aktif
hesaplanıyor ama kullanılmıyor (`METIN-BILANCO-DENETIMI.md` D).

---

## 12 vaka

```
 # hisse  konu                                       tutar    oran impact emniyet  skor  karar
 1 THYAO  Yeni Sözleşme İmzalanması              15,000 mn    %1.1    4.5    1.00  4.50  özet
 2 MTRKS  Yeni Sözleşme İmzalanması               1,000 mn   %31.2   10.0    1.00 10.00  PUSH
 3 MTRKS  Yeni Sözleşme İmzalanması              15,000 mn  %468.8   10.0    0.25  2.50  PUSH (belirsiz)
 4 MTRKS  Temettü Dağıtımı                          500 mn   %15.6    7.5    1.00  7.50  PUSH
 5 MTRKS  Bedelli Sermaye Artırımı                1,000 mn   %31.2    7.0    1.00  7.00  PUSH
 6 MTRKS  Vergi Cezası                              250 mn    %7.8    2.5    1.00  2.50  sessiz
 7 MTRKS  Konkordato Talebi                              —       —    0.0    0.45  0.00  sessiz
 8 MTRKS  Finansal Rapor                                 —       —      —       —     —  yenileme adayı
 9 MTRKS  Özel Durum Açıklaması (Genel)             800 mn   %25.0    6.5    1.00  6.50  PUSH
10 MTRKS  Yeni Sözleşme İmzalanması                      —       —    2.5    0.70  1.75  sessiz
11 MTRKS  Pay Bazında Devre Kesici Bildirimi             —       —      —       —     —  mekanik
12 MTRKS  SPK İşlem Yasağı / Pay Duyurusu                —       —    0.0    0.45  0.00  sessiz
```

## Vaka vaka: metin → sayı → karar

### 1 · THYAO, 15 milyar TL sözleşme → **özet** (4.50)
```
özet : Şirketimiz ile yurt dışı müşteri arasında sözleşme imzalanmıştır.
gövde: İmzalanan sözleşmenin toplam bedeli 15 milyar TL'dir; teslimat 24 ayda tamamlanacaktır.
```
Tutar gövdeden: 15.000.000.000. Ciro 1,36 trilyon → **%1.1** → `kucuk` +2.0,
konu `sözleşme` +2.5 → impact 4.5 → özet. **Dünyanın en büyük ihalesi bile dev
şirkette özet.**

### 2 · MTRKS, 1 milyar TL sözleşme → **PUSH** (10.00)
Aynı cümlenin küçük şirketteki hâli: %31.2 → `devasa` +8.5 → impact 10.
**Aynı haber, farklı şirket: özet ↔ push.** Sistemin karakteri bu.

### 3 · MTRKS, 15 milyar TL sözleşme → **PUSH (belirsiz)**
%468.8 oran → `implausible_ratio` cezası ×0.25 → skor 2.50 ama impact 10.0.
Mesaj etiketli gider (bu turda eklendi):

```
MTRKS · Yeni Sözleşme İmzalanması
impact 10.0 · emniyet 0.25 · skor 2.50
Şirketimiz yeni bir sözleşme imzalamıştır.
konu: sözleşme (+2.5) | oran: %468.75 (devasa +8.5) | impact: 10.0
⚠️ belirsiz: implausible_ratio — tutar/oran doğrulanmadan gönderildi
```

Ciro 3,2 mlr olan şirketin 15 mlr sözleşme alması muhtemelen **payda hatası**
(belki grup cirosu, belki kümülatif). Sistem "biliyorum" demiyor, **etiketliyor**.

### 4 · MTRKS, 500 mn TL temettü → **PUSH** (7.50)
%15.6 → `buyuk` +6.5, konu `temettü` +1.0 → 7.5. Nakit dönüşü gerçekten önemli
sinyal; küçük şirkette büyük.

### 5 · MTRKS, 1 milyar TL **bedelli** sermaye artırımı → **PUSH** (7.00)
`bedelli` önseli **−1.5** ama kova +8.5 → 7.0. Yani sistem bu haberi **olumsuz
olarak işaretlemiyor**: mesajda "bedelli" yazıyor, "risk" yazmıyor. Seyreltme
haberi ile sözleşme haberi aynı listede, aynı ölçekte (`ONEM-DENETIMI.md` F9).

### 6 · MTRKS, 250 mn TL vergi cezası → **sessiz** (2.50)
%7.8 → `orta` +4.0, `ceza` önseli −1.5 → impact 2.5 → sessiz. **Kötü haber
sessizliğe gömülüyor**; önsel negatif olduğu için ceza, haberi "önemsiz" yapıyor.

### 7 · MTRKS, konkordato talebi → **sessiz** (0.00)
Tutar yok → impact = konu önseli; `konkordato` önsel listesinde **yok** → 0.
Şirketin hayatını etkileyen haber, sistemin gözünde **hiçbir şey**.

### 8 · MTRKS, "Finansal Rapor" → **yenileme adayı**
Sınıf `fr` → `fundamentals_refresh`. Bu rota bildirimi **paydanın kontrolü için ayırır**; ne gövdedeki “net kâr 600 mn” bir haber skoru olur ne de etiket tek başına mali tablonun yenilendiğini kanıtlar.

### 9 · MTRKS, "Özel Durum Açıklaması (Genel)" + 800 mn sipariş → **PUSH** (6.50)
Konu bilinmiyor (genel başlık), önsel 0. Karar **tamamen büyüklükten**: %25 →
+6.5. Sipariş mi, dava mı, fabrika mı — sistem okumuyor.

### 10 · MTRKS, 500 milyon **dolar** sözleşme → **sessiz** (1.75)
`fx_fallback`: tutar var ama kur yok → 0.70 ceza, impact yalnız konu (2.5).
**Döviz bazlı her KAP haberi bugün kör.**

### 11 · MTRKS, devre kesici → **mekanik**
Triyajda düşer: kaydedilir, skorlanmaz. (Sayılması gerekirdi — "3+ devre kesici"
uyarısı henüz kodda yok, `HABER-DAVRANISI.md` F7.)

### 12 · MTRKS, SPK işlem yasağı → **sessiz** (0.00)
Triyaj bu satırı "önemli DKB" olarak hattan geçirir (doğru), ama tutar yok ve
konu önseli yok → impact 0 → sessiz. **Gerçek bilgi şoku, sıfır puan.**

---

## Sahte alıcıdaki ilk mesaj örnekleri

Önce push'lar (önem sırasıyla), en sonda tek özet:

```
[5001:MTRKS]
MTRKS · Yeni Sözleşme İmzalanması
impact 10.0 · emniyet 1.00 · skor 10.00
Şirketimiz yeni bir sözleşme imzalamıştır.
konu: sözleşme (+2.5) | oran: %31.25 (devasa +8.5) | impact: 10.0

[5002:MTRKS]
MTRKS · Yeni Sözleşme İmzalanması
impact 10.0 · emniyet 0.25 · skor 2.50
Şirketimiz yeni bir sözleşme imzalamıştır.
konu: sözleşme (+2.5) | oran: %468.75 (devasa +8.5) | impact: 10.0
⚠️ belirsiz: implausible_ratio — tutar/oran doğrulanmadan gönderildi

[5003:MTRKS]
MTRKS · Temettü Dağıtımı
impact 7.5 · emniyet 1.00 · skor 7.50
Genel kurul temettü dağıtımını onaylamıştır.
konu: temettü (+1.0) | oran: %15.62 (buyuk +6.5) | impact: 7.5

[digest:2026-11-17]
GÜNLÜK ÖZET · 17.11.2026
3 olay · 3 push ayrıca gönderildi

1. MTRKS · Bedelli Sermaye Artırımı
   skor 7.00 · impact 7.0 · oran %31.2 · bütçe
2. MTRKS · Özel Durum Açıklaması (Genel)
   skor 6.50 · impact 6.5 · oran %25.0 · bütçe
3. THYAO · Yeni Sözleşme İmzalanması
   skor 4.50 · impact 4.5 · oran %1.1
```

Günlük bütçe (3/hisse) yüzünden 5 push adayından 3'ü gitti; **bedelli (7.00) ve
genel ÖDA (6.50) düşürülmedi**, özete indi. TELEGRAM mesajlarında da sıra skor
sırası: bütçeden düşen 7.00, özetteki 6.50'nin üstünde.

---

## Bu vakaların gösterdiği 4 şey

1. **Aynı haber, farklı şirket = farklı karar** (vaka 1 ↔ 2). Bu tasarım gereği;
   oran paydaya bağlı. Sistemi "haber" değil, "haber/bilanço" olarak okumak şart.
2. **Tutar anahtardır.** Vaka 3, 6, 7, 10, 12: tutar yoksa ya da çıkmıyorsa
   karar neredeyse tamamen kayboluyor (impact 0-2.5).
3. **Kötü haber ile iyi haber aynı kefede** (vaka 5 vs 2): yön yok, etiket yok.
4. **Bilançonun 5/6'sı boşta**: 12 vakanın hepsi tek bir sayıya (ciro TTM)
   dayanıyor; özkaynak/net kâr/PD karara girmiyor.

## Tekrar üretmek

```bash
.venv/bin/python scripts/ornek_vakalar.py
.venv/bin/python scripts/ornek_vakalar.py --only-actions
```
