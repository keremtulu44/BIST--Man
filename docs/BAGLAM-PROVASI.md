# Haberden yoruma: bağlam provası · 18 Kasım 2026

**Kapsam: kanıt paketi deneyi.** Pilot, şirket açıklaması ile piyasa gündeminin aynı olmadığını sınadı. “Hazır” burada `build_packet` sonucudur; hisse alarmına iki paket gideceği anlamına gelmez. [Güncel üründe](ANA-MANTIK.md) yalnız ilgili şirketten en çok bir gelişme döner. Piyasa paketi ve bağımsız AI çağrısı o yola bağlı değildir.

> **İlk ölçüm ve bugünkü durum:** Aşağıdaki **%200**, ilk provada gerçekten gözlenen kural hatasıdır. Sonraki düzeltmeyle aynı girdide kural hattı da **%20** buluyor; duruş, imzalı EUR satışı ve endeks duyurusu özete giriyor. Güncel karşılaştırma ve alıcı sonuçları: [Gönderim akışı](GONDERIM-AKISI.md). Bu belge ilk ölçümü korur.

## Neyi ölçtük?

`list_light_live.json` dosyasındaki **17 Kasım KAP kesitini** mevcut triyaj ve tutar ayrıştırıcısından geçirdik: **397** satır, **151** rutin, **127** mekanik, **119** aday. Bu adayların **0/119** tanesinde başlık + özetten parasal tutar çıkarıldı. Bu, gövdede tutar olmadığı anlamına gelmez. Yalnızca listeyi alıp yanına bütün mali tabloyu eklemek, eksik olan haber bilgisini sağlamıyor.

Ardından **4 arşiv kaydı ve 3 sentetik vakayı** mevcut `process_batch` hattı ve deneysel paketleyiciyle işledik. Pilot için konu ve kanıt cümleleri elle seçildi. Test edilenler: alıntının kaynağı, doğru hisse/kapsam, tarihli payda, eksik veri kararı ve dışarı çıkacak JSON. **Aşağıdaki okumalar editör örneği; bir AI yanıtı veya fiyat tahmini değil.**

| Vaka | Deney sonucu | Güvenle çıkarılabilecek sonuç / sınır |
|---|---|---|
| 24 aylık satış sözleşmesi · **sentetik** | Hisse paketi, 474 karakter. Eski hat **%200**, seçilmiş kanıtla **%20**. | İki yıllık sipariş, şirketin bir yıllık satışlarının beşte biri büyüklüğünde. Teslimatlar satışları destekleyebilir; toplam bedel bu yılın geliri veya kârı değil. |
| Döküm hattında duruş · **sentetik** | Eski karar `silent`; pilotta parasal tahmin içermeyen hisse paketi, 261 karakter. | O hattın durması teslimatları geciktirebilir. Süre ve kapasite payı bilinmeden şirket geneli için kayıp hesaplanamaz. |
| Borsa İstanbul endeks duyurusu · **KAP 1698956** | Eskiden `unresolved_ticker`; şimdi **tek piyasa gündemi** paketi, 273 karakter; hisse veya bilanço alanı yok. | Fiili dolaşım oranındaki değişiklik ilgili payların endeksteki ağırlıklarını etkileyebilir. Başlık hangi payların değiştiğini veya yürürlük tarihini vermiyor. |
| MTRKS söylenti açıklaması · **KAP 1699030** | 4.257 karakterlik temiz Türkçe gövdeden, incelemenin kapsamı ve olumsuzluk ifadesi birlikte korundu: 402 karakter alıntı, 585 karakter paket. | MTRKS, atıfta bulunduğu SPK incelemesinde şirket veya yöneticileri hakkında açıklanmış yaptırım olmadığını **belirtiyor**. Bu açıklama yeni bir para cezası olarak yorumlanmamalı. |
| 10 milyon EUR sipariş · **sentetik** | 309 karakter; özgün döviz tutarı var, `tarihli_kur_yok`. TL karşılığı ve ciro oranı yok. | Sipariş haberi kaybolmuyor; kıyas için gereken kur uydurulmuyor. |
| FONET ihale başlığı · **KAP 1698981** | **Bekletildi.** İhale süreci başlık/özetinden satış paketi üretilmedi. | Özet sonucun ne olduğunu söylemiyor. Kazandı/kaybetti diyebilmek için detay gerekli. |
| Borsa İstanbul “Hak Kullanımı” · **KAP 1698691** | **Bekletildi.** Piyasa geneli olarak gönderme denemesi reddedildi. | Yayıncının Borsa İstanbul olması tek başına tüm piyasayı etkileyen bir karar olduğunu göstermiyor. |

**Özet:** 5 paket hazır, 2 bekletildi; medyan **309**, en büyük **585** karakter. Sınır kompakt JSON için **1.000 Unicode karakter**; bu token, ücret veya doğruluk ölçümü değil. Sığmayan kanıt kesilmiyor. Gerçek API/Telegram çağrısı: **0**.

## Yönü belirleyen hata: doğru pay, doğru payda

Sözleşme metninde **2 milyar TL ana proje bütçesi** ile **200 milyon TL şirketin kendi satış bedeli** birlikte geçiyor. İlk provadaki “en büyük tutar” kuralı ilkini alıp 1 milyar TL ciroya bölüyordu. Aşağıdaki paket yalnız şirkete ait satış bedelini belirten tam cümleyi kullanıyor. Örnek sayılar ve mali tablo **sentetik**; gerçek bir KAP bildirimi gibi adreslenmedi.

```json
{"konu":"satis_sozlesmesi","kaynak":"senaryo:1","yayin":"2026-11-17T10:00:00+00:00","kapsam":"hisse","hisse":"ORNEK","kanit_yeri":"govde","kanit":"Şirketimiz bu proje kapsamında 24 ayda teslim edilecek ürünler için 200 milyon TL bedelli satış sözleşmesi imzalamıştır.","tutar":{"deger":"200000000","birim":"TRY"},"olcek":{"ttm_ciro_try":1000000000,"bedel_ciro_yuzde":20.0,"donem_sonu":"2026-06-30","tablo_yayini":"2026-08-12T09:00:00+00:00","kaynak":"senaryo:mali-tablo-1"}}
```

İki saat **sonra** yayımlanan 2 milyar TL cirolu tablo da deney girdisindeydi. Kullanılsaydı oran %10 olurdu. Dönem sonu geçmişte olsa bile haber saatinde bilinmeyen sürüm kullanılamaz. Net kâr veya özkaynağı tek başına değiştirmek ise sözleşme paketini bir karakter dahi değiştirmiyor. Alıntıda iki ayrı bedel kalırsa pilot en büyüğünü seçmek yerine oranı vermiyor.

## Pilotun gösterdiği doğrulama ihtiyacı

1. Farklı şirket ve tarihlerden **30–50 gerçek gövde** topla. İmzalanmış satışın yanında alım, iptal, konsorsiyum, üretime ara verme/yeniden başlama ve şirketle ilişkilendirilemeyen bir piyasa duyurusu da olsun. Her kayda doğru kapsamı, tam kanıt cümlesini ve o anda yayımlanmış mali raporu (yoksa “yok”) elle işaretle.
2. **Önce kanıt seçimini test et.** Yeni örneklerde sistemin seçtiği tutar gerçekten şirketin payı mı? “Yaptırım yok” ifadesinden yanlışlıkla yaptırım mı çıkmış? Kaç kritik haber bekletilmiş? Yanlış hisse, gelecek tarihli payda veya tersine çevrilmiş bir karar varsa canlıya geçme. Alıntının metinde birebir bulunması da yeterli değil: sonraki paragrafta bir fesih veya ön koşul olabilir. Tek günlük liste ve önceden etiketlenmiş yedi vaka, otomatik seçimin doğruluğunu ölçmeye yetmez.
3. Son okumayı elle seçilmiş kanıtla karşılaştır. Kaynaksız rakam, kapsamı kaybolmuş bir “yaptırım yok” ifadesi veya gerekçesiz fiyat yönü hatadır. Pilot sırasında düşünülen ayrı model deneyi, ürünün entegrasyon adımı değildir: sonuç ana sistemin mevcut grafik yorum isteğine eklenir.

## Tekrar çalıştırma

README’deki sanal ortamla depo kökünde:

```sh
python scripts/baglam_provasi.py             # tam JSON, güncel kural kararı, bekletme nedeni, sahte gönderim
python -m pytest tests/test_context_packet.py  # kanıt, zaman ve paket sınırları
python -m pytest                              # mevcut hattın testleri dahil
```

Pilot mevcut hattın triyaj ve hisse eşlemesini kullanır, ancak gönderim kararını veya skorunu değiştirmez. Kalibrasyon ayarlarına ve kayıtlı verilere yazmaz.
