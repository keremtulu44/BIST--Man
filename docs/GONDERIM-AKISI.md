# Önceki gönderim motoru ile kanıt paketinin karşılaştırması · 18 Kasım 2026

> Bu belge iki eski deneyin sonuçlarını karşılaştırır. Aşağıdaki “push/özet” kararları **alarm tetikleyicisi değildir**. Ana sistemin çağıracağı [yerel sorgu](ALARM-BAGLAMI.md) bunların hiçbirini göndermeden çalışır; [ürün amacı](ANA-MANTIK.md) ayrıdır.

**Ana değişiklik:** Şirket için büyük bir sayı bulmakla o şirkete ait bir bedeli doğrulamak artık aynı şey değil. Bir haberin özete girmesi için de mutlaka parasal oran üretmek gerekmiyor.

## Aynı yedi kaydı iki ayrı yoldan geçirdik

İlk yol `process_batch → rank → dispatch`: kısa kural mesajını **sahte alıcıda** gözlemledik. İkincisi `build_packet`: ileride yorum için kullanılabilecek kanıt paketini üretir veya nedeniyle bekletir; bunu Telegram'a ya da bir modele bağlamadık. `hazır` bir gönderim kararı değildir.

| Kayıt | İlk provada kural hattı | Düzeltmeden sonra sahte alıcı için plan | Pilotun kanıt paketi |
|---|---|---|---|
| 24 aylık satış · sentetik | Ana proje 2 milyar TL / ciro 1 milyar TL = **%200**, belirsiz push | **Push:** şirket bedeli **200 milyon TL**, oran **%20**. İki saat sonra yayımlanan tablo kullanılmadı. Bu satış ölçeği; kâr tahmini değil. | Hazır |
| Döküm hattında duruş · sentetik | Sessiz | **Özet:** duruşun süresi ve hattın kapsamı teslimatlar açısından önemli. Kayıp tutarı yok. | Hazır |
| Borsa İstanbul · **1698956** | Hisse bulunamadı | **Tek piyasa gündemi:** fiili dolaşım değişikliği endeks ağırlıklarını etkileyebilir. Bir hisseye veya bilançoya bağlanmadı. | Hazır |
| Şirketin söylentiye yanıtı · **1699030** | Sessiz | **Yine sessiz:** her söylenti yeni bir yaptırım olarak yükseltilmiyor. | Hazır; MTRKS'nin açıklamasındaki “yaptırım kararı bulunmamaktadır” ifadesi kapsamıyla birlikte korunuyor |
| 10 milyon EUR imzalı satış · sentetik | Sessiz | **Özet:** asıl döviz tutarı ve satışa olası katkı var; kullanılabilir kur yoksa TL ve oran yok. | Hazır |
| FONET ihale sonucu · **1698981** | Sessiz | **Sessiz:** başlık ve özet ihalenin kazanıldığını göstermiyor. | Beklet: sonuç için detay gerekli |
| Borsa İstanbul “Hak Kullanımı” · **1698691** | Hisse bulunamadı | **Gönderilmedi:** yayıncı tek başına piyasa genelini kanıtlamıyor. | Beklet: kapsam doğrulanmadı |

**Sonuç:** 4 arşiv kaydı + 3 senaryo → sahte alıcıda **1 ayrı push ve 3 olaylık 1 özet**. Aynı girdide **5 hazır paket, 2 bekletme**. Bunlar iki ayrı sayım; gerçek gönderim ve AI çağrısı **0**.

## Yanlış bildirimi önleyen kontroller

- **Kaynak ve bedel:** Bozuk liste alanı yalnız kendi satırında raporlanır. KAP'tan başka `disclosureIndex` gelirse detay reddedilir. Kimlikli detayla çalışan hatlar ayrıca yayın zamanını ve varsa yapısal hisseyi kontrol eder. Başlık, özet ve gövde birbirine yapıştırılmaz. Sözleşme tutarı için bedel/pay ifadesi aranır; proje toplamı veya pay başı fiyat şirketin toplu bedeli değildir. İki olası bedel kalırsa en büyüğü ya da TL'ye çevrilebileni seçilmez. Detay sağlayıcısı varsa TL'ye çevrilebilen fiyatlı sözleşmenin veya açık imza beyanının bedeli, detaydaki düzeltme/fesih açısından da kontrol edilir. Tek kayıttaki kaynak hatası sonraki kaydı durdurmaz.
- **Payda:** `PublishedRow` sürümleri verilirse skor ve paket aynı haber-anı seçiminden geçer. Yanlış hisse, başka para birimi veya ileri dönem oran üretemez. Eski tek-snapshot girişi hâlâ desteklenir; yayın saati bilinmediği not edilir. Bu yol geçmişe dönük isabet kanıtı değildir. Pakette ise tarihi doğrulanamayan payda hiç kullanılmaz. Skora elle `fx` veriliyorsa olay anına ait olmasını çağıran sağlamalıdır; paket bu tarihsiz eşlemeyle döviz çevirmez.
- **Kanal:** Rutin/fon ve pay bazlı devre kesici gönderilmez. Açık finansal rapor başlığı, sınıfı olmayan liste kaydında da **yenileme adayıdır**; veri yenilenmiş sayılmaz. Tek hisseye ait “Üretime Ara Verilmesi”, “Konkordato Talebi” ve “SPK İşlem Yasağı Nedeniyle Pay Duyurusu” başlıkları ölçeksiz **günlük özet** yoluna sahiptir. Piyasa yolu şimdilik yalnız sınanan Borsa İstanbul endeks konusudur. Bu kurallar eşikleri veya geçmiş ölçüm skorlarını yükseltmez.
- **Metin:** İmzalı satışı niteleyen açık iptal/koşul varsa yeni satış oranı verilmez; paketleyici bağlamıyla çelişen bedel alıntısını da bekletir. Uzun push özetinin sonu kesilip olumsuzluk tersine çevrilmez. **9.007** karakterlik tek satır önce 4.000'e kesiliyordu; şimdi **4.000 + 4.000 + 1.007** olarak birleştiğinde sonundaki “İPTAL.” dahil aynı metindir. Emoji parçalanmaz.

## “Planlandı” ile “gönderildi” arasındaki sınır

`rank` yan etkisiz plandır. Bütçe aynı TRT günü ve hisse için bu çağrıdaki sıralı adaylara uygulanır; 4. ve 5. aday ayrı numaralarla özete iner. `dispatch` tek gün kabul eder ve doğrudan gönderim denenmiş sonuçları yeniden göndermez.

| Durum | Gözlemlenen sonuç |
|---|---|
| Push adayı var, alıcı yok | `push_candidates=1`, `push_attempted=0`, `notified=0` |
| Alıcı reddetti veya hata verdi | Denendi, onaylanmadı. Sonraki mesajlar denenir; özet gerçekte kaç push'ın onaylandığını yazar. Başarısız özet “yok” değildir. |
| Telegram `HTTP 200`, ama `ok: false`, bozuk JSON veya HTML | Başarı değil. Çok parçalı mesajda ilk başarısız parçadan sonra devam edilmez. |
| `sendMessage` sonrası yanıt kayboldu | İlk mesaj gitmiş olabilir. Körlemesine yeniden POST edilmez. `failed` **tam mesajın onaylanmadığı** anlamına gelir; kullanıcıya hiç parça ulaşmadığı anlamına gelmez. |

Bağlantı kurulamadığında ve `429` yanıtında sınırlı tekrar sürer. `ok: true` sağlayıcı onayıdır, kullanıcının okuduğuna dair kanıt değildir. Çağrılar arası kalıcı tekilleştirme/bütçe, canlı zamanlayıcı ve tarihli kur kaynağı henüz yok.

## Tekrar çalıştırma

```sh
python scripts/baglam_provasi.py    # 7 kayıt: paket ve ayrı kural gönderiminin tam metni
python scripts/ornek_vakalar.py     # 12 senaryo: 3 ayrı push, 6 olaylık özet (2'si bütçeden)
python -m pytest                    # kayıtlı KAP + sahte/HTTP mock alıcı, anahtar gerekmez
```

İmza/bedel kuralları dar Türkçe kalıplardır; genel bir haber okuyucusu değildir. Paket için konu ve tam alıntı hâlâ etiketlidir. Üretime geçiş ölçütü değişmedi: farklı şirketlerden gerçek gövdelerde yanlış hisse, yanlış bedel, olumsuzluk ve kaçan önemli olaylar ayrıca ölçülmeli.
