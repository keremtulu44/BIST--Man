# Ana mantık: grafik alarmının yanına KAP bağlamı

**Kullanım amacı:** Grafiğim bir hisse için alarm verdiğinde, o anda o şirket hakkında bilinen son anlamlı gelişmeyi de görmek istiyorum. Haberleri tek tek bildiren ikinci bir sistem istemiyorum. Sorulacak soru **“O saatte bu şirket ne açıklamıştı?”**; “Fiyat neden hareket etti?” değil.

Bu belge ürün kararını anlatır. [Veri kaynakları](VERI-KAYNAKLARI.md) girdilerin sınırlarını, [alarm sorgusu](ALARM-BAGLAMI.md) ise kodun bugünkü sözleşmesini gösterir.

## Kim, ne zaman ne yapar?

| Zaman | BIST-Man’ın görevi | Ana sistemin görevi |
|---|---|---|
| Haber geldiğinde | Kaydı ayır, hangi hisseye ait olduğunu belirle, anlamı doğrulanabilen kanıtı **sessizce** sakla. | Haber geldi diye grafik alarmı, yorum isteği veya kullanıcı mesajı başlatma. |
| Grafik alarmı tetiklendiğinde | Verilen **hisse + alarm zamanı** için arşivden en çok bir gelişme döndür. | Alarmı üret, yerel fonksiyonu çağır, sonucu **mevcut grafik yorum isteğine** ekle. Birlikte yorumlama ve kullanıcıya sunum burada kalır. |

**Birleşme hedefi tek dosya, tek uygulama, tek Render dağıtımıdır.** Yukarıdaki ayrım iki servis değil, aynı uygulamadaki yazma ve okuma işleridir. BIST-Man için HTTP uç noktası, ayrı model çağrısı veya Telegram gönderimi gerekmez. Bağlam bulunamazsa grafik alarmı yine işlenmelidir.

**Bugün:** Depo henüz ana sistemle birleştirilmiş tek dosya değildir. Python modülleri, kayıtlı KAP örneklerini hazırlayan bir CLI ve yerel bir SQLite arşivi vardır. Canlı kaynağa erişebilen bir istemci olması, arşivin 7/24 güncellendiği anlamına gelmez. Alarm sorgusu ne KAP’a gider ne de grafiği indirir.

## Toplanan satırdan anlamlı gelişmeye

**KAP sınıfı, olayın konusu ve kullanıcıya verilecek cevap üç farklı şeydir.** Örneğin `ODA` bir bildirim sınıfıdır; “imzalanmış satış” anlamına gelmez. İlk elemeden geçmek de arşivde hazır cevap olmak değildir.

**1. Listeyi oku.** KAP listesi bildirim numarasını, yayın zamanını, unvanı, konuyu ve varsa özeti verir. Hisse kodu ve sınıf eksik olabilir. Bozuk satır, nedeniyle birlikte `rejected` içinde ayrılır; diğer satırlar işlenebilir. Boş yanıt “piyasada haber yok” kanıtı değildir.

**2. Sınıf ve konuyla ön eleme yap.** [Triyaj](../src/bistman/triage.py) ağ isteği yapmadan dört sonuçtan birini verir:

| Sonuç | Hangi kayıt? | Sonraki adım |
|---|---|---|
| `routine` | Portföy dağılım raporu veya şirket genel bilgi formu gibi rutin konu | Hisse bağlamına alma. |
| `mechanical` | Devre kesici gibi mekanik bildirim | İşlem kesintisini şirketin kendi açıklaması gibi sunma. |
| `fundamentals_refresh` | FR sınıfı veya sınıfı eksik ama açıkça “Finansal Rapor” başlıklı kayıt | Mali tabloyu kontrol etme **adayı** olarak ayır. Etiket, tablonun yenilendiğini kanıtlamaz. |
| `pipeline` | Rutin olmayan ÖDA, DKB içinde işlem yasağı, DG içinde sermaye artırımı gibi adaylar | Atıf ve kanıtı incele. Sınıf yoksa konu/özet kuralları uygulanır; tanınmayan satır da kaybolmasın diye aday kalır. |

**3. Adayı tek bir hisseyle ilişkilendir.** Önce varsa yapısal kod kullanılır. O yoksa birden çok sembollü başlık reddedilir; kalanlarda normalleştirilmiş unvan ve gerekirse başlıktaki tek sembol denenir. Eşleşmeyen fon, belirsiz şirket ve piyasa gündemi tek hisseye yazılmaz. Detay kullanılıyorsa bildirim numarası, yayın anı ve varsa yapısal kod seçilen kayıt/hisseyle uyuşmalıdır.

**4. “Ne oldu?” sorusuna kaynak göster.** Bugünkü hisse konuları `satis_sozlesmesi`, `uretim_durusu` ve `sirket_aciklamasi`. Konuyu ve belirleyici alıntıyı **inceleyen kişi seçer**; kod özet veya Türkçe gövdede tam alanı ya da cümle sınırlarındaki alıntıyı doğrular. Başlık tek başına yetmez. İhale başlığı sözleşmenin kazanıldığını göstermez. `endeks_parametresi` pilotta piyasa konusudur; hisse cevabı değildir.

**5. Uygun sürümü sessizce kaydet.** `record`, kullanılabilir sürüm için `hazir` döndürür. Kanıt yetersiz veya çelişkiliyse nedenini bildirir. `kapsam_disi` yeni sürüm yazmaz: alarm arşivi, KAP listesinin ham kopyası değildir. Bunlar **yazma sonuçlarıdır**, gönderim kararı değil. Eski motorda `silent` kalan [MTRKS açıklaması](ALARM-BAGLAMI.md) burada hazırdır: şirketin ne dediği belli, sayısal puan gerekmiyor.

Mali tablo **isteğe bağlı bir kıyas** içindir. Örneğin imzalı satışta şirketin kendi bedeli, haber yayımlandığında bilinen karşılaştırılabilir son 12 aylık ciroya oranlanabilir. Ana proje bütçesi şirketin payı değildir; sözleşme bedeli de kâr değildir. Tarihli tablo veya uygun kur yoksa oran verilmez; doğrulanmış olay yine anlatılabilir. Duruşta süre ve kapasite bilinmeden zarar tahmin edilmez.

## “Son” hangi ana göre?

- `published_at`: KAP yayın zamanı. `known_at`: **o sürümün** kanıtı ve okumasıyla kullanılabilir olduğu ilk an. `alarm_zamani`: ana sistemin gerçek tetik anı; isteğin işlendiği saat değil. Seçim için `published_at ≤ known_at ≤ alarm_zamani` gerekir. Örneğin 14:53’te yayımlanıp 15:02’de hazır olan detay, 15:00 alarmına girmemelidir.
- Doğru hisse ve kaynaktaki, o anda geçerli sürümler arasından **yayını en yeni olan** seçilir (eşitlikte büyük bildirim no). Varsayılan pencere yayın üzerinden **7 × 24 saat**; varsa `valid_until` anı dahil değildir. Rutin bir form, başka hisse veya yüksek push puanı bu sırayı değiştirmez. Yedi gün içinde olmak, bugünkü hareketi açıklamak demek değildir.
- Cevap kısa bir **olay**, desteklenebiliyorsa **neden önemli olabileceği**, **yayın zamanı ve kaynak** içerir. Haber listesi, grafik verisi veya skor gönderilmez. Uygun kayıt yoksa `durum: "yok"` ve **“İlgili güncel gelişme bulunamadı.”** döner. Bu, arşivle sınırlı bir sonuçtur.

## Açık kontroller

Aşağıdakiler henüz uygulanmış düzeltmeler değil, kayıtlı ve sentetik örneklerle saptanan sınırlar ve kabul ölçütleridir.

| Bugünkü davranış | Birleşmeden önce kabul ölçütü |
|---|---|
| Aynı sürüm tekrar yazıldığında iki `known_at` değerinin küçüğü tutuluyor. Geç yüklenen detaya yanlışlıkla yayın saati verilirse geçmiş bir cevap `yok`tan `var`a dönebiliyor. | Gerçek ilk kullanılabilir anı canlı kayıtta koru; varsayımsal geçmiş yüklemesini ayır. 15:02’de hazır olan kanıt tekrar işlense de 15:00 cevabı değişmemeli. |
| MTRKS’nin gerçek liste satırında kod yok; unvan eşleşmezse hat detaya geçmeden durabiliyor. Ayrı bir sentetik örnekte ise önce tek hisseye yazılan kaydın sonraki sürümü çok hisseli çıktı; `kapsam_disi` eski atfı geri çekmedi. | Eşleşmeyen şirket adayının kimliğini hazırlık sırasında kontrol et; yanlış atfı, yanlışlığı öğrenildiği andan itibaren kullanma. Başka bir bildirimin detayını düzeltme sayma. |
| FONET ihalesinde sonuç henüz doğrulanamadığında da uygun haber hiç bulunmadığında da dışarıya `yok` dönebiliyor. Son başarılı taramanın hangi saate kadar tamamlandığı tutulmuyor. Okunamayan DB ise `yok` değil, hatadır. | Taranmış dönem, detay bekleyen aday ve erişim hatası ayırt edilebilmeli. Eksik veriyi kesin yokluk diye sunmadan grafik yoluna devam et. |
| Tam alıntı kontrolü anlam kontrolü değildir: “duruş gerçekleşmemiştir” yanlışlıkla `uretim_durusu` etiketlenirse duruş yorumu eklenebiliyor. | Olumsuzluk, yeniden başlama, iptal ve düzeltmeyi tam metinle sına. Anlamı doğrulanmayan yorumu verme; kaynaklı olay da kurulamıyorsa beklet. |
| Gövde hiç değişmemişken yanlış bir editör alıntısıyla yeniden kayıt, önceki doğru cevabı bastırabiliyor. Ayrı numaralı düzeltme hedeften önce yüklenirse bağ kurulmadan reddediliyor. | Editör hatasını kaynaktaki gerçek değişiklikten ayır; sırasız gelen düzeltmeyi hedefi geldikten sonra yeniden işle. Gerçek geri çekme eski satışı diriltmemeli. |

Başarı ölçüsü **kaç mesaj gönderildiği veya fiyat yönünün tutması değil**: yanlış hisse, ileri tarihli kanıt, tersine çevrilmiş anlam ve kaçan doğrulanabilir gelişme sayısıdır. Bir sonraki veri kontrolü için farklı şirketlerden 30–50 gerçek gövdede bu dört hatayı ayrı ayrı saymak önerilir; mevcut küçük prova otomatik sınıflandırma başarısı ölçümü değildir. [Ağsız kontrolü tekrarla](DOGRULAMA-ADIMLARI.md).
