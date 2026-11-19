# Alarm geldiğinde: yerel çağrı ve yanıt

[Ana mantık](ANA-MANTIK.md) **haber kaydı** ile **grafik tetiklenince okuma** işlerini ayırır. Burada ikinci işin çalışan Python sözleşmesi var. Aynı uygulamada çağrılacak bir fonksiyondur; ayrı Render, ağ adresi veya haberden ana sisteme giden bir webhook yoktur.

## Ana sisteme birleşme örneği

Aşağıdaki örnek mevcut modüllerle çalışır; grafik ve yorum API’si bu depoda değildir. `yorum_istegi` ana sistemin **bu alarm için** hazırladığı yeni istektir. KAP bağlamı için ikinci bir yorum isteği oluşturulmaz.

```python
import asyncio
from bistman.alarm_context import kap_baglami_ekle
from bistman.collector import KapCollectorService

# 1) Uygulama başlangıcında arka plan sessiz KAP toplayıcısı (mesaj/alarm üretmez):
servis = KapCollectorService(arsiv_yolu, universe=izlenen_hisseler)
toplayici_gorevi = asyncio.create_task(servis.run_forever(interval_seconds=180, warmup_on_start=True))

# 2) Ana sistemin grafik alarmı tetiklendiğinde (salt okunur, WAL ile eşzamanlı güvenli):
sonuc = kap_baglami_ekle(yorum_istegi, arsiv_yolu, hisse, tetik_zamani)
# durum == "var" ise yorum_istegi["kap_baglami"] (ve daha yeni bekleyen kayıt varsa
# yorum_istegi["kap_bekleyen_bildirim"]) eklenir.
# durum == "yok" ise varsa eski kap_baglami/kap_bekleyen_bildirim temizlenir;
# sonuc["neden"] boşluk sebebini verir.
# Hata durumunda None döner, uyarı loglanır; "haber yok" uydurulmaz ve alarm akışı kesilmez.
```

Ana projeye paket yerine tek dosya olarak eklemek için: `python scripts/tek_dosya_uret.py hedef/kap_baglam.py --dogrula` (yalnız standart kütüphane + `httpx`; eski skor/Telegram kodu dahil edilmez).

- **Girdi:** `MTRKS`, `mtrks`, `BIST:MTRKS`, `MTRKS.E` ve `MTRKS.IS` aynı hisseye gider; rakamla biten endeks/varant kodları (`XU100`, `THYAO1`) reddedilir. Tek BIST pay sembolü ve saat dilimli `datetime` gerekir. Alarm gecikmeli işlenirse `şimdi` değil, gerçek tetik zamanı kullanılır.
- **`durum: "var"`:** `baglam` içinde `konu`, `olay`, `yayin_zamani` ve `kaynak` vardır. Destekleniyorsa `onemi`, mali kıyas gösteriliyorsa ayrıca `mali_tablo` kaynağı ve dönemi gelir. Seçilen doğrulanmış haberden daha yeni ama henüz detayı/incelemesi bekleyen bir bildirim varsa üst düzeyde `bekleyen_bildirim` notu da döner. `olay` birebir alıntı **veya** incelenmiş kısa okumadır; `onemi` şirketin birebir beyanı değildir.
- **`durum: "yok"`:** `baglam: null`, `mesaj: "İlgili güncel gelişme bulunamadı."` ve ek olarak `neden` alanı gelir:
  - `uygun_gelisme_yok`: tarama alarm anına kadar tamamlanmış, güncel uygun kayıt yok (`son_tarama` alanı döner),
  - `detay_bekleniyor`: pencerede hisseye ait aday var ama detay/inceleme henüz tamamlanmamış,
  - `tarama_geride`: son tarama alarm anının gerisinde kalmış (`son_tarama` alanı döner),
  - `tarama_kaydi_yok`: arşivde henüz tarama damgası yok,
  - `surum_celiskisi`: eş güçte çelişen iki sürüm nedeniyle kayıt bekletildi.
- **Hata:** Biçimsiz kod veya saat dilimsiz zaman `ValueError`; olmayan/okunamayan SQLite arşivi hatadır. `alarm_baglami()` bu durumları normal bir `yok` cevabına çevirmez (`kap_baglami_ekle()` ise loglayıp `None` döner). CLI hatada sıfırdan farklı kodla çıkar.
- **Eşzamanlılık:** Arşiv SQLite WAL kipinde ve `busy_timeout` ile açılır; arka plan toplayıcısı yazarken grafik alarmı aynı dosyadan veya `asyncio.to_thread` içinden kilitlenmeden salt okunur sorgu yapabilir.

## Sessiz toplama ve bir kaydı hazırlamak

Canlı akışta [`KapCollectorService`](../src/bistman/collector.py) ve `sync_once`, ortak KAP `/list/light` listesini okur; rutin, fon/varant ve düşük sinyalli idari bildirimleri eler; `/tr/bist-sirketler` şirket listesini aynı SQLite dosyasında önbellekler (geçici KAP hatasında bayat önbellekle devam eder); kurum başlıklı (`KAP`/`SPK`/`BİST`) satırların detayını denetler; restart sonrası `warmup` ile izlenen hisselerin son penceresini doldurur ve eski imleç/tarama kayıtlarını otomatik budar.

Kural tabanlı kanıt seçici (`extract_verified_evidence`) yalnız kaynak metindeki birebir tam cümleleri seçer: imzalı satış/ihracat/yapım sözleşmesi ve kesin siparişler, açık sözleşme fesih/iptalleri, üretim/faaliyet duruşları, kesinleşen ihale kazanımları, bedelsiz/bedelli/tahsisli sermaye artırımları, kâr payı kararları, pay geri alım programı kararları, VBTS/brüt takas/pay sırası kapatma/SPK işlem yasağı/idari para cezası tedbirleri, haber/söylenti netleştirmeleri, varlık alım-satım/tesis devreye alma/grev/lisans/konkordato/beklenti revizyonu/birleşme/kredi notu kararları ve pay alım-satım eşik bildirimleri. İptal/opsiyon/şart/olumsuzluk sınanır; doğrulanamayan aday uydurulmadan `detay_bekleniyor` olarak kaydedilir. İsteğe bağlı `EvidenceReviewer` önerileri de aynı `record()` denetimlerinden geçer.

Yazma tarafı açıkça `open_archive(path, writable=True)` kullanır. `arsiv.record(item, origin="kap_arsiv", known_at=..., topic=..., quote=..., detail=...)` için `item` hisse eşlemesinden geçmiş bir aday (`ArchiveCandidate` veya `ProcessedDisclosure`) olmalıdır. İsteğe bağlı mali tablo `statements` ile verilir; sorgu o anda tablo indirmez. [`scripts/alarm_baglami.py`](../scripts/alarm_baglami.py) hem ağsız `hazirla-demo` / `sorgula` hem de tek turluk `tara-canli` komutlarını içerir.

Kayıtta doğrulanmış alıntının paketi ile alarm için okunacak kısa metin ayrı saklanır. Canlı kayıtta var olan bir sürümün `known_at` saati yeniden işlemeyle geri çekilemez (yalnız açık tarihsel günlük testi için `replay=True` vardır). Yalnız `sirket_aciklamasi` için isteğe bağlı `reviewed=ReviewedReading(event=..., significance=...)` verilebilir; iki alan da dolu, toplamları en fazla 400 karakter olmalı ve alıntının olumsuz/yalanlama yönünü tersine çevirmemelidir.

### Zaman ve düzeltme kuralları

- **Ayrı olaylar:** Sorgu varsayılan olarak yalnız `origin="kap_arsiv"` sürümlerini okur. `sentetik` test verisiyle karışmaz. Yayın ve bilinirlik alarm anını aşamaz. Yayın penceresi 7 gündür (alt sınır dahil); `max_age=timedelta(hours=24)` gibi pozitif bir süre verilebilir. `valid_until` anı artık geçerli değildir. Uygun olaylar arasında en yeni yayın, eşitlikte büyük bildirim numarası kazanır.
- **Aynı bildirim numarası:** Gövdede doğrulanan sürüm normalde özetten güçlüdür; yalnızca bir detay dosyası bulunması özeti güçlendirmez. Eş güçte daha sonra bilinen sürüm geçerlidir. Eş güç ve aynı bilinirlik anında çelişki varsa ikisinden biri seçilmez. Güçlü sürüm bekletilmişse eski olumlu satış/duruş geri gelmez; özetteki açık fesih/koşul veya üretime dönüş de önceki kanıtı geçersiz kılar. Sonradan çok hisseli veya kapsam dışı olduğu anlaşılan kayıt kendi gözlem anından itibaren geri çekilir; değişmemiş kaynakta yapılan alıntı/okuma hatası ise mevcut doğrulanmış cevabı silmez.
- **Farklı numaralı düzeltme:** `supersedes` açık bir bağdır; başlık benzerliğinden çıkarılmaz. Yükleyici önceki **aynı kaynak ve güncel hisseye** ait kaydı göstermeli, düzeltme doğrulanmış gövde kanıtı taşımalıdır. Geçerliyse önceki kayıt düzeltmenin bilindiği andan itibaren elenir; düzeltmenin süresi dolunca eski kayıt geri dönmez. Canlı akışta sıra dışı gelen düzeltmeler `defer_missing_target=True` ile hedef gelene kadar bekletilebilir; hedef doğrulandığında otomatik bağlanır.

Bu kurallar gerçek ilk bilinirlik saatinin kendiliğinden bulunduğu veya her yanlış etiketin yakalandığı anlamına gelmez. [Uygulanan güvenlik kontrolleri](ANA-MANTIK.md#uygulanan-güvenlik-kontrolleri) · [sorgu ve sürüm testleri](../tests/test_alarm_context.py).

## Gerçek arşiv örneği: MTRKS

[Kurulumdan](../README.md#hızlı-başlangıç) sonra depo kökünde, sanal ortamın Python’u ile:

```sh
python scripts/alarm_baglami.py hazirla-demo kap-demo.sqlite  # yeni dosyaya, yalnız bir kez
python scripts/alarm_baglami.py sorgula kap-demo.sqlite MTRKS 2026-11-17T14:50:00+03:00
python scripts/alarm_baglami.py sorgula kap-demo.sqlite MTRKS 2026-11-17T15:00:00+03:00
```

14:50 sorgusu `yok` döner. 15:00 sorgusunun cevabı:

```json
{
  "hisse": "MTRKS",
  "alarm_zamani": "2026-11-17T12:00:00+00:00",
  "durum": "var",
  "baglam": {
    "konu": "sirket_aciklamasi",
    "olay": "MTRKS, SPK’nın 2024/35 sayılı bültenindeki incelemede kendisine veya yöneticilerine yönelik bir yaptırım açıklanmadığını belirtti.",
    "onemi": "Şirket, payındaki işlemlere ilişkin eski kararları açıklığa kavuşturuyor; yeni bir ceza bildirmiyor.",
    "yayin_zamani": "2026-11-17T11:53:07+00:00",
    "kaynak": "https://www.kap.org.tr/tr/Bildirim/1699030"
  }
}
```

**14:53:07 Türkiye saati** yayındır (11:53:07 UTC). Arşiv kesitinde detayın ilk geliş veya okumanın tamamlanma saati ölçülmedi. **Yalnız demoda** `known_at = published_at` varsayılır. Bu iki cevap canlı sistemin gecikme ölçümü değildir.
