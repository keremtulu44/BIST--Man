# Araştırma aracı: gönderim eşiklerinin kalibrasyonu (eski Faz 4)

> Bu komut eski skor ile sonraki fiyat hareketi arasındaki ilişkiyi araştırır. [Alarm bağlamı](ANA-MANTIK.md) için fiyat indirmek, eşik seçmek veya bu koşuyu yapmak gerekmez. Fiyatla eşleşme bir haberin hareketin nedeni olduğunu göstermez.

---

## Tek komut

```powershell
.\.venv\Scripts\python.exe scripts\kalibrasyon_gercek.py --limit 5
```

- `--limit 5` = ilk 5 hisse. Önce küçük koşu: ne indirdiğini gör, sonra büyüt.
- Süre; hisse sayısına, önbelleğe ve kaynağın yanıtına bağlıdır.
- Sonuç iki dosyaya yazılır: `docs\kalibrasyon-sonuc.md` ve
  `config\scoring.measured.toml`.

Önce [README’deki kurulumu](../README.md#hızlı-başlangıç) yap: KAP istemcisi
`httpx` kullanır. `--dry-run` ise ağ kullanmaz; raporu yalnız terminale yazar.
Böylece bir demo, önceki gerçek ölçümlerin üzerine yazmaz.

## Ne indiriyor (maliyet)

| Adım | İstek | Not |
|---|---|---|
| KAP şirket listesi | 1 | `/tr/bist-sirketler` → `{kod: OID}` |
| Üye geçmişi | 1 / hisse | 1 istek = 1 yıl |
| Mali tablo | 3 / şablon | 8 çeyrek + 4 yıl; gerekirse banka şablonu da denenir |
| **Bildirim gövdesi** | Gereken aday başına 1 | Listede sonuç veya şirkete ait bedel eksikse detaya bakılır |
| Fiyat | 1 / hisse + 1 | hisse = Yahoo, endeks = İş Yatırım |

Örneğin boş önbellek, 47 hisse, hisse başına tek şablon ve 300 gövde için
1 + 47 + 141 + 300 + 48 = 537 istek gerekir (yeniden denemeler hariç).
Gövdeler `data\bodies\`, fiyatlar `data\prices\` altında önbelleklenir;
yeniden koşuda geçmiş ve mali tablolar yine çekilir. İki önbellek de Git dışında.

## Tutar hakkında ölçülen şey

İncelenen **89 satırlık MTRKS geçmiş tablosunun** başlık ve özetlerinden tutar çıkarılamadı. Bu örnekte yalnız tabloyla kurulan skorlar bu yüzden düşüktür. Genelde bir özette bedel olabilir; tersine, gövde gelmesi de şirketin kendi bedelinin saptandığı anlamına gelmez. Nitel bir açıklamayı grafik bağlamına almak için bedel gerekmez.

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

## Üretilen dosyalar

Ağ kullanan araştırma koşusu şu iki dosyaya yazar; `--dry-run` yazmaz:

```
docs/kalibrasyon-sonuc.md
config/scoring.measured.toml
```

`data/prices/` ve `data/bodies/` **gönderme** (onlarca MB, önbellek).

## Ölçümün sınırı

1. Rapor okunur: eşik tablosu ayırt edici mi, kaçan maddi olay var mı.
2. **Tek güncel bilanço tüm geçmişe uygulanıyor.** Bu koşu veri kapsamını ölçmek
   için kullanılabilir; geçmişteki karar isabetini kanıtlamaz. Haberin yayın
   anında elde olan bilançolarla tekrar ölçmeden ve ayrı bir dönemde sınamadan
   eşikleri `config/scoring.toml`'a taşımayın.
3. Bu araştırma, alarm arşivinin hangi ana kadar tarandığını veya tek bir şirket olayının doğru etiketlendiğini ölçmez; bunların [ayrı kabul ölçütleri](ANA-MANTIK.md#açık-kontroller) vardır.
