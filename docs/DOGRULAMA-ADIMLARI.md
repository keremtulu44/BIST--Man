# Veri toplama ve sınıflandırma kontrolü

[Ana mantığı](ANA-MANTIK.md) sınamak için canlı hesap, yeni indirme, dal değiştirme veya fixture dosyalarının üzerine yazma gerekmez. [Geliştirme kurulumunu](../README.md#hızlı-başlangıç) yaptıktan sonra depo kökünde sanal ortamın Python’unu kullan. Örneğin PowerShell’de aşağıdaki `python` yerine `.\.venv\Scripts\python.exe` yazabilirsin.

## 1. Listeyi ayır: “aday” henüz “hazır” değil

```sh
python scripts/baglam_provasi.py
```

[17 Kasım 2026 listesi](../tests/fixtures/kap/list_light_live.json): **397 satır, 0 reddedilen; 151 rutin, 127 mekanik, 119 aday.** 119 adayın hiçbirinde başlık + özetten parasal tutar çıkarılmadı. Bu ölçüm yalnız bu listeye aittir; “gövdelerde tutar yok” veya “119 haber kullanıcıya gidecek” demek değildir.

Komutun devamındaki **4 gerçek + 3 sentetik** paket deneyi 5 hazır / 2 bekletilen sonucu verir; piyasa gündemi paketi de buna dahildir. En sondaki gönderim çıktısı eski motorun **sahte alıcı** deneyidir. İkisi de hisse/zaman sorgusunun cevap sayısı değildir.

## 2. İncelenmiş şirket bağlamını sorgula

```sh
python scripts/alarm_baglami.py hazirla-demo kontrol.sqlite
python scripts/alarm_baglami.py sorgula kontrol.sqlite MTRKS 2026-11-17T14:50:00+03:00
python scripts/alarm_baglami.py sorgula kontrol.sqlite MTRKS 2026-11-17T15:00:00+03:00
python scripts/alarm_baglami.py sorgula kontrol.sqlite FONET 2026-11-17T15:00:00+03:00
```

- İlk komut yalnız **yeni bir dosyada** çalışır. Listenin tamamını değil, **4 incelenmiş gerçek kaydı** işler: MTRKS hazır, FONET ihale sonucu için detay gerekli, iki Borsa İstanbul kaydı hisse bağlamı dışında. Sentetik örnekler bu arşive girmedi.
- MTRKS: yayın öncesi `yok`, sonrası `var` ve kaynak **1699030**. Deney bilinirlik saati olarak yayın saatini varsayar. Şirket, ilgili incelemede yöneticileri veya kendisi hakkında yaptırım açıklanmadığını belirtiyor; yeni bir cezadan söz etmiyor.
- FONET: `yok`. [1698981 numaralı bildirim](https://www.kap.org.tr/tr/Bildirim/1698981) var ama bu kesitte sonuç kanıtlanamıyor; “ihaleyi kazandı/kaybetti” denmemeli.

## 3. Sınırları test et

```sh
python -m pytest tests/test_kap_source.py tests/test_triage.py tests/test_ticker_map.py
python -m pytest tests/test_context_packet.py tests/test_alarm_context.py tests/test_alarm_context_cli.py
python -m pytest
```

İlk grup kaynak biçimini, ön elemeyi ve hisse eşlemesini; ikincisi alıntı, zaman, gerçek/sentetik ayrımı, düzeltme ve salt okunur sorguyu sınar. Testlerin geçmesi, bütün KAP haberlerinin otomatik olarak doğru sınıflandırıldığı veya veri akışının güncel olduğu anlamına gelmez. [Birleşme öncesi açık kontroller](ANA-MANTIK.md#açık-kontroller) ayrıca izlenir.

### Eski doğrulama çıktıları ne için?

[THYAO/AKBNK ölçümleri](GERCEK-VERI-RAPORU.md) ve `scripts/dogrulama/` mali tablo/TTM araştırmasına aittir. Kümülatif sütunlarda paket TTM’inin dört sütunun toplamına eşit çıkması doğru payda kanıtı değildir. borsapy kurmak, `kap_fixture_kaydet.ps1` ile bugünün listesini eski örneğin üzerine indirmek veya fiyat kalibrasyonu çalıştırmak bu kontrole hazırlık adımı **değildir**.
