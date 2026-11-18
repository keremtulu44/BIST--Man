# Belgeleri okuma sırası

## Güncel ürün

| Soru | Belge |
|---|---|
| Kullanıcının amacı ne? Haber ile grafik alarmının sorumluluğu nerede ayrılıyor? | **[Ana mantık ve sınıflandırma](ANA-MANTIK.md)** — ürün kararları ve açık kontroller için ana belge |
| Hangi KAP kaydı, hangi alan ve hangi mali tablo kullanılabilir? | [Veri kaynakları](VERI-KAYNAKLARI.md) |
| Ana sistem hangi fonksiyonu çağıracak, cevap ve hata nasıl okunacak? | [Alarm bağlamı](ALARM-BAGLAMI.md) |
| Kayıtlı örneklerle bu davranışı nasıl doğrularım? | [Doğrulama adımları](DOGRULAMA-ADIMLARI.md) · [kurulum](../README.md#hızlı-başlangıç) |

## Kararların dayandığı deneyler

- [Bağlam provası](BAGLAM-PROVASI.md): Hisse ve piyasa kapsamını ayıran paket deneyi. Her hazır paket hisse cevabı değildir.
- [Gönderim akışı karşılaştırması](GONDERIM-AKISI.md): Aynı olaya kanıt paketi ile eski gönderim motoru neden farklı davranıyor?
- [Gerçek veri raporu](GERCEK-VERI-RAPORU.md) ve [kod incelemesi](KOD-INCELEMESI.md): Mali payda, hisse atfı ve kaynak doğrulama bulgularının geçmişi.

## Önceki gönderim motoru ve araştırma arşivi

Bu belgelerdeki “v1”, “üretim”, “kesin karar” ve “sıradaki faz” ifadeleri **ilgili deneyin yazıldığı aşamaya** aittir. Aşağıdakiler güncel ürünün kurulumu, tetikleyicisi veya iş listesi değildir. Eski yollar, kod ve raporlardaki bölüm referansları için korunur.

| Kapsam | Belgeler |
|---|---|
| İlk planlar (Postgres kuyruğu, dispatcher, emtia, ayrı model fikirleri) | [Plan v1](PLAN-V1.md) · [Mimari v0.1](MIMARI.md) · [Tasarım kararları](TASARIM-DETAY.md) · [LLM taslağı](DURUM-VE-LLM-PLANI.md) |
| İlk aşamalarda ne ölçülmüştü? | [Durum raporu B1–B34](DURUM-RAPORU.md) · [Haber davranışı](HABER-DAVRANISI.md) · [Önem denetimi](ONEM-DENETIMI.md) · [Metin/bilanço denetimi](METIN-BILANCO-DENETIMI.md) |
| Gönderim motorunun ve kalibrasyon araçlarının kullanımı | [Sıralama ve digest](SIRALAMA-VE-DIGEST.md) · [12 örnek vaka](ORNEK-VAKALAR.md) · [Faz 4 kalibrasyon](FAZ4-KALIBRASYON.md) |
| İncelenen ama alarm sorgusu için kurulmayan kaynaklar | [borsapy incelemesi](VERI-KATMANI-BORSAPY.md) · [ilk sağlayıcı karşılaştırması](arsiv/KAYNAK-ARASTIRMASI.md) |

**Ölçüm dosyaları** ([TTM kontrolü](dogrulama-sonuc.json), [izleyen kontrol](dogrulama-sonuc3.json), [kalibrasyon çıktısı](kalibrasyon-sonuc.md)) yazıldıkları koşuyu kaydeder. Özellikle `DRY-RUN (sentetik fiyat)` bir piyasa başarısı ölçümü değildir. Bunlar güncel ürün politikası olarak düzenlenmez.
