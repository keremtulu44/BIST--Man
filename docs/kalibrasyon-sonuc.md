# Kalibrasyon Sonucu

Üretim: 2026-11-17 13:23 UTC · DRY-RUN (sentetik fiyat)

## Kapsam

- Hisse: **1** · KAP'ta bulunamayan: 0
- Bildirim satırı: **86** (atfedilen: 86, %100)
- Çok sembollü duyuru: 4 · kesik ilgili listesi: 0
- Fiyat tepkisi hesaplanan olay: **20** · |z|≥2.0: **2**

Satır seçimi (sessiz eleme yok):

- `bulk_platform`: 6
- `fund_flow`: 3

## Skor dağılımı

- olay: 20 · en düşük 0.00 · medyan 0.00 · en yüksek 0.45
- digest eşiğini (3.5) geçen: 0 · push eşiğini (6.5) geçen: 0

> ⚠️ **Hiçbir olay push eşiğini geçmedi.** Eşik tablosu bu örnekte
> ayırt edici değil — nedeni genelde tutarın bulunamamasıdır (gövde
> indirilmediyse skor yalnız konu önselinden gelir). `--no-bodies` ile
> koştuysan tabloyu öyle okuma.

> ⚠️ **Örneklem küçük (20 olay).** Tablo karar için değil,
> mekanizma içindir. Eşik seçimi en az birkaç yüz olay ister.

## Eşik × bütçe ızgarası

```
varyant                      eşik=3.5  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=3.5  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=5.0  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=5.0  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=5.5  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=5.5  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=6.0  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=6.0  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=6.5  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=6.5  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=7.0  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=7.0  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=7.5  bütçe=3   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
varyant                      eşik=7.5  bütçe=∞   push/gün= 0.00 isabet=    — ort|CAR|=      — kaçan=2
```

Okunuşu: `isabet` = gönderilenler içinde |z|≥2 olanların oranı; `kaçan` =
maddi olduğu halde gönderilmeyenler. Yüksek eşik isabeti artırır, kaçanı da
artırır. Karar bu iki sayının hangisi daha pahalı olduğuna bağlı.

## Mevcut ayar

- push eşiği: **6.5** · digest: 3.5
- günlük push bütçesi: 3

## Sıradaki adım

1. Örneklemi büyüt (evrenin tamamı, 2 yıl).
2. `kaçan` maddi olayları tek tek oku: kural mı kaçırdı, eşik mi yüksek?
3. Seçilen eşikleri `config/scoring.toml`'a **elle** taşı (bu dosya kanıt kalır).
