# BIST--Man

**BIST-Man, ana sistemin grafik alarmına o hisseyle ilgili KAP bağlamı eklemek içindir.** Alarmı kendisi üretmez. Ana sistem hisseyi ve tetik zamanını verir; arşiv o anda bilinen en güncel uygun gelişmeyi, yayın saati ve kaynağıyla döndürür. Bir açıklamanın fiyat hareketine neden olduğunu iddia etmez.

**[Amaç, veri toplama ve sınıflandırma: ana mantık →](docs/ANA-MANTIK.md)**

| Haber geldiğinde | Grafik alarmı geldiğinde |
|---|---|
| Kaydı ele, şirketi ve kanıtı doğrula, sessizce arşivle. Yorum isteği veya mesaj başlatma. | `arsiv.alarm_baglami(hisse, alarm_zamani)` ile arşivi oku. Varsa gelişmeyi ana sistemin **mevcut** grafik yorum isteğine ekle. |

**Hedef:** Ana sistemle tek dosyada birleştirmek; tek uygulama ve tek Render dağıtımı. Ayrı HTTP servisi ya da ikinci bir AI çağrısı tasarlanmıyor. **Bugün:** Arka plan sessiz KAP toplayıcısı (`KapCollectorService`: şirket listesi önbelleği, restart sonrası ısınma, otomatik budama), eşzamanlı SQLite WAL arşivi (`bistman.alarm_context`), ana sistem okuma yardımcısı (`kap_baglami_ekle`) ve eski gönderim/skor kodunu dışarıda bırakan tek dosya üreticisi (`scripts/tek_dosya_uret.py`) hazır.

## Hızlı başlangıç

Python 3.11 veya üzeriyle depo kökünde:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python scripts/alarm_baglami.py hazirla-demo kap-demo.sqlite  # bir kez, yeni dosya
.venv/bin/python scripts/alarm_baglami.py sorgula kap-demo.sqlite MTRKS 2026-11-17T14:50:00+03:00
.venv/bin/python scripts/alarm_baglami.py sorgula kap-demo.sqlite MTRKS 2026-11-17T15:00:00+03:00
.venv/bin/python scripts/tek_dosya_uret.py kap_baglam.py --dogrula  # ana sisteme tek dosya üret
.venv/bin/python -m pytest
```

Windows’ta `python -m venv .venv` ile başlayıp `.venv/bin/python` yerine `.\.venv\Scripts\python.exe` kullan. Kurulumdan sonra demo ve testler ağsız çalışır; bot, API anahtarı veya haricî veritabanı gerekmez.

**Beklenen fark:** 14:50’de “İlgili güncel gelişme bulunamadı.” 15:00’te ise MTRKS’nin [14:53:07’de yayımlanan açıklaması](https://www.kap.org.tr/tr/Bildirim/1699030) döner: şirket, ilgili SPK incelemesinde kendisi veya yöneticileri hakkında yaptırım açıklanmadığını belirtiyor. Yeni bir ceza haberi değildir. Yalnız bu örnekte kanıt yayın anında hazır varsayılmıştır; gerçek ilk görülme saati ölçülmemiştir.

## Nereden devam etmeli?

- [Veri kaynakları](docs/VERI-KAYNAKLARI.md): KAP listesinin verdiği bilgi, ne zaman detay gerektiği ve mali tablonun sınırı.
- [Yerel fonksiyon ve cevap örneği](docs/ALARM-BAGLAMI.md): ana sisteme eklenecek alan, sonuç bulunmaması ve hata davranışı.
- [Doğrulama adımları](docs/DOGRULAMA-ADIMLARI.md): kayıtlı listeyi, sınıfları ve zaman sınırını yeniden kontrol etme.
- [Belge dizini](docs/README.md): güncel belgeler, deney sonuçları ve eski taslaklar. Depodaki skor, push/özet, Telegram ve fiyat kalibrasyonu **önceki gönderim motoruna** aittir. Bunlar ne bağlam seçme ölçütü ne de bu ürünün dağıtım planıdır.

Kişisel/eğitim amaçlıdır. Kaynakların kullanım koşulları geçerlidir; yatırım tavsiyesi değildir.
