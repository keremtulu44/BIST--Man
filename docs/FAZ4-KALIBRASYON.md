# Faz 4 — Gerçek Kalibrasyon Nasıl Koşulur

Bu dosya **senin makinende** (Windows, PowerShell) çalıştırılacak tek komutu ve
çıktının nasıl okunacağını anlatır. Sandbox'ta ağ yok; bu yüzden canlı koşu
sende, kod ve testler bende.

---

## Tek komut

```powershell
python scripts\kalibrasyon_gercek.py --limit 5
```

- `--limit 5` = ilk 5 hisse. Önce küçük koşu: ne indirdiğini gör, sonra büyüt.
- Küçük koşu ~30 saniye, tam koşu (47 hisse) birkaç dakika.
- Sonuç iki dosyaya yazılır: `docs\kalibrasyon-sonuc.md` ve
  `config\scoring.measured.toml`.

Kurulum yok, `pip install` yok: fiyat/KAP/İş Yatırım istemcilerinin hepsi
standart kütüphane.

## Ne indiriyor (maliyet)

| Adım | İstek | Not |
|---|---|---|
| KAP şirket listesi | 1 | `/tr/bist-sirketler` → `{kod: OID}` |
| Üye geçmişi | 1 / hisse | 1 istek = 1 yıl |
| Mali tablo | 2 / hisse | sanayi, tutmazsa banka şablonu |
| **Bildirim gövdesi** | 1 / aday | **tutar yalnız burada** |
| Fiyat | 1 / hisse + 1 | hisse = Yahoo, endeks = İş Yatırım |

47 hisse için ~50 geçmiş + ~300 gövde + ~48 fiyat ≈ 400 istek. Gövdeler
`data\bodies\`, fiyatlar `data\prices\` altında önbelleklenir; **ikinci koşu
bedava**. (Bu iki klasör `.gitignore`'da, commit edilmez.)

## En önemli gerçek

**KAP geçmiş tablosunda tutar yok.** Tablo yalnız tarih, sınıf, unvan, konu,
özet veriyor. Tutar bildirim gövdesinde. Bu yüzden:

- `--no-bodies` ile koşarsan skorların neredeyse tamamı **0** çıkar.
- Bu bir hata değil: eşiği ölçmek için tutar şart, tutar için gövde şart.

Dry-run'ın (sentetik fiyat, 89 gerçek satır) çıktısı tam olarak bunu gösterdi:

```
olay: 20 · en düşük 0.00 · medyan 0.00 · en yüksek 0.45
digest eşiğini (3.5) geçen: 0 · push eşiğini (6.5) geçen: 0
```

Yani mekanizma çalışıyor, **ölçüm için tutar eksik**. Gövde indirildiğinde aynı
hat 1 milyar TL'lik bir tutarı %31 ciro oranına çevirip skoru 2.4'ten 6'nın
üstüne taşıyor (bkz. `tests/test_backfill.py::test_body_provider_changes_the_score`).

## Rapordaki sayılar nasıl okunur

`docs/kalibrasyon-sonuc.md` bölümleri:

1. **Kapsam** — kaç hisse, kaç satır, kaçı atfedildi, kaç olay fiyatla eşleşti.
   `atfedilen %100` değilse atıf kuralı eksik.
2. **Satır seçimi** — sessiz eleme yok, hepsi sayılır:
   - `fund_flow` = fon/portföyün çok hisseli bildirimi (İş Portföy "Pay Alım
     Satım"). Hisse bazlı bilgi yok → dışlanır.
   - `bulk_platform` = MKK'nın onlarca hisseli duyurusu (SPK işlem yasağı, tipe
     dönüşüm). Gerçek bilgi şoku → **tutulur**, sadece sayılır.
3. **Skor dağılımı** — eşik tablosunun ayırt edici olup olmadığının kanıtı.
   `push eşiğini geçen: 0` ise tabloyu okuma, önce tutar kaynağını düzelt.
4. **Eşik × bütçe ızgarası** — `isabet` / `kaçan`. Karar burada verilir.
5. **Hesaplanamayan nedenleri** — `no_price_series`, `no_session_after_publish`,
   `no_volatility`… Bunlar örneklem kaybıdır, gizlenmez.

## Geri gönderirken

İki dosyayı commit'le (gitignore'da **değiller**):

```
docs/kalibrasyon-sonuc.md
config/scoring.measured.toml
```

`data/prices/` ve `data/bodies/` **gönderme** (onlarca MB, önbellek).

## Sonra ne olacak

1. Rapor okunur: eşik tablosu ayırt edici mi, kaçan maddi olay var mı.
2. Seçilen eşikler `config/scoring.toml`'a **elle** taşınır; ölçüm dosyası kanıt
   olarak kalır (sezgiyle ayar yapılan yer ile ölçümün yazıldığı yer ayrı).
3. Ondan sonra Faz 2c (Postgres + dedup/cursor) ve Telegram dispatcher.
