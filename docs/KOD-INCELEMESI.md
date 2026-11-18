# Önceki motorun kod incelemesi: haberden karara (18 Kasım 2026)

> Skor ve gönderim hattına ait inceleme kaydıdır. Aşağıdaki işler bugünkü ürün planı değildir. Aynı veri kurallarının hangi bölümünün alarm bağlamında kullanıldığı [Ana mantık](ANA-MANTIK.md) belgesindedir.

Çekirdeğin korunacak tarafı şu: KAP bildiriminin **hangi şirkete ait olduğu**, **ne kadar büyük olduğu** ve **bildirilip bildirilmeyeceği** ayrı adımlarda belirleniyor. `process_batch` kaydedilmiş haberler ve enjekte edilen mali tablolarla çalışıyor. Bir model veya grafik projesi olmadan bu kararları tekrar üretip sınamak mümkün. Buna karşılık çalışan bir 7/24 servis, kalıcı gönderim kaydı veya doğrulanmış bir isabet oranı henüz yok.

## Düzeltilen ve ağsız testle doğrulanan bulgular

| Nerede | Önceki sorun → yeni davranış |
|---|---|
| `parsing/money.py` | `5 MİLYAR TL` tüm grubu `KeyError` ile durdurabiliyordu. Birimler artık Türkçe ve ASCII büyük harfle de okunuyor. `€3.500,50`, `250 $` ve açıkça birim taşıyan `2000 TL` kaybolmuyor. Yüzdeler kaynak metinden silinmediği için `text[m.start:m.end] == m.raw`; tutarın gerçekten nereden geldiği kontrol edilebiliyor. |
| `parsing/kap_table.py`, `pipeline.py` | Geçmiş tablosunda `A1CAP` kodu kaybolabiliyor, ilgili hisselerde `CAP` olabiliyordu. Kod artık bütün olarak okunuyor. Canlı yolda birden çok yapısal kod varsa veya yapısal kod yokken başlıkta birden çok sembol varsa ilk hisse seçilmiyor. Üye geçmişinin açıkça atadığı tek kod korunuyor. |
| `pipeline.py` | Mali tablo kaynağındaki kalite bayrakları skordan önce kayboluyordu. Örnek testte 60 milyar TL sözleşme / 500 milyar TL ciro = %12. Sağlam tabloda skor **9,00 → anlık bildirim**. Aynı sayılara `ttm_gap` ve `stale_fundamentals` eklenince güven `0,60 × 0,85`, skor **4,59 → özet**. Eşikler ve ağırlıklar değişmedi. |
| Veri toplama ve ölçüm | Sertifika hatasında varsayılan olarak doğrulamasız indirmeye geçilmiyor. Kök sertifika sorunu varsa güvenilen sertifika deposu düzeltilmeli. `--dry-run` sentetik raporu yalnız ekrana yazar; önceki gerçek ölçümü ezmez. Depodan geliştirme kurulumu, KAP/Telegram için zaten kullanılan `httpx` bağımlılığını da kurar. |

## İncelemede önerilen takip işleri

1. **Önce geçmiş veride zaman tutarlılığı.** `scripts/kalibrasyon_gercek.py`, hisse başına bir güncel mali tablo alıp bir yıllık habere uyguluyor. Örneğin marttaki sözleşmeyi haziranda biten dönemin cirosuna bölmek, martta verilemeyecek bir karar üretir. Dönem sonu tek başına yeterli değil: hesapta yalnız **haber anına kadar yayımlanmış** tablo sürümü kullanılmalı. Kabul testi: sonradan yayımlanan tabloyu eklemek, eski haberin skorunu değiştirmemeli; uygun tablo yoksa oran boş kalmalı.
2. **Haber okuması için bir referans seti.** Başlangıç için 30–50 KAP bildirimini elle etiketle: bildirim no, hisse, ana tutarın birebir alıntısı ve para birimi, olay türü, tutar olmasa da önemli mi? Birden çok tutar içeren sözleşmeleri, dövizli haberleri ve konkordato gibi **parasal tutar belirtilmeden de önemli olabilecek** olayları özellikle seç. İlk incelemede `best_amount` ile en büyük çevrilebilir tutarı seçme sorunu vardı. Sonraki kanıt ve pay kontrolleri için bkz. [gönderim karşılaştırması](GONDERIM-AKISI.md). Başarı ölçüsü yalnız fiyat tepkisi olmasın: yanlış şirkete atıf, yanlış ana tutar ve kaçan kritik haberleri ayrı say. İleride kuralla modeli aynı örnekler üzerinde karşılaştırabilirsin.
3. **Eşiği değiştirmeden önce deneyle ürünü eşleştir.** `sources/prices.build_url` hisse tarafında `days` değerini URL'ye koymuyor; kısa bir serinin uzun pencere sayılmadığı doğrulanmalı. `analysis/calibration.select_pushes` bütçeyi **tüm gün**, `digest.rank` ise çağrı içinde **hisse başına** uyguluyor. Aynı örnek girdiyle aynı gönderim politikası ölçülmeden “en iyi eşik” seçilmemeli.
4. **Canlı gönderimden önce tekrar güvenliği.** Önce kayıtlı bir günü iki kere işlet: `(bildirim no, hisse)` başına bir gönderim olmalı. Süreç yeniden başlayınca son işlenen kayıt ve günün bütçesi korunmalı. Bugünkü grup içi tekilleştirme bunun yerini tutmuyor.

**Doğrulama sınırı:** README’deki geliştirme kurulumuyla `python -m pytest`, `python scripts/hat_demusu.py` ve `python scripts/kalibrasyon_gercek.py --dry-run`. Son komutta 89 kayıtlık geçmişten 3 fon akışı ayrılıyor, 20 olay sentetik fiyatla eşleşiyor. Bu bir işleyiş testi; canlı KAP/Telegram, gerçek piyasa başarısı veya bir AI sağlayıcısı test edilmedi.
