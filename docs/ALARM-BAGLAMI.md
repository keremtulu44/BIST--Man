# Alarm geldiğinde: yerel çağrı ve yanıt

[Ana mantık](ANA-MANTIK.md) **haber kaydı** ile **grafik tetiklenince okuma** işlerini ayırır. Burada ikinci işin çalışan Python sözleşmesi var. Aynı uygulamada çağrılacak bir fonksiyondur; ayrı Render, ağ adresi veya haberden ana sisteme giden bir webhook yoktur.

## Ana sisteme birleşme örneği

Aşağıdaki örnek mevcut modüllerle çalışır; grafik ve yorum API’si bu depoda değildir. `yorum_istegi` ana sistemin **bu alarm için** hazırladığı yeni istektir. KAP bağlamı için ikinci bir yorum isteği oluşturulmaz.

```python
import logging
import sqlite3
from datetime import datetime
from bistman.alarm_context import open_archive


def kap_baglami_ekle(yorum_istegi: dict, arsiv_yolu: str, hisse: str, tetik_zamani: datetime) -> None:
    try:
        with open_archive(arsiv_yolu) as arsiv:  # mevcut dosya, salt okunur
            sonuc = arsiv.alarm_baglami(hisse, tetik_zamani)
        if sonuc["durum"] == "var":
            yorum_istegi["kap_baglami"] = sonuc["baglam"]
    except (ValueError, sqlite3.Error) as hata:
        # Örnek çağıran politikası: hatayı kaydet, "haber yok" uydurma.
        logging.warning("%s için KAP bağlamı eklenemedi: %s", hisse, hata)
    # Ana sistem elindeki yorum_istegi ile kendi akışına devam eder.
```

- **Girdi:** `MTRKS`, `mtrks` ve `BIST:MTRKS` aynı hisseye gider. Tek sembol ve saat dilimli `datetime` gerekir. Alarm gecikmeli işlenirse `şimdi` değil, gerçek tetik zamanı kullanılır.
- **`durum: "var"`:** `baglam` içinde `konu`, `olay`, `yayin_zamani` ve `kaynak` vardır. Destekleniyorsa `onemi`, mali kıyas gösteriliyorsa ayrıca `mali_tablo` kaynağı ve dönemi gelir. `olay` birebir alıntı **veya** incelenmiş kısa okumadır; `onemi` şirketin birebir beyanı değildir.
- **`durum: "yok"`:** `baglam: null`, `mesaj: "İlgili güncel gelişme bulunamadı."` Bugün bu, arşivde uygun kanıt bulunmadığını söyler; detay beklenmesini veya tarama eksikliğini ayrı kodlamaz.
- **Hata:** Biçimsiz kod veya saat dilimsiz zaman `ValueError`; olmayan/okunamayan SQLite arşivi hatadır. Fonksiyon bu durumları normal bir `yok` cevabına çevirmez. CLI hatada sıfırdan farklı kodla çıkar.

## Bir kaydı hazırlamak başka, onu sorgulamak başka

Yazma tarafı açıkça `open_archive(path, writable=True)` kullanır. `arsiv.record(item, origin="kap_arsiv", known_at=..., topic=..., quote=..., detail=...)` için `item` hisse eşlemesinden geçmiş bir `ProcessedDisclosure` olmalıdır. İsteğe bağlı mali tablo `statements` ile verilir; sorgu o anda tablo indirmez. [`hazirla-demo` örneği](../scripts/alarm_baglami.py) bildirim alıcısı kullanmaz.

Kayıtta doğrulanmış alıntının paketi ile alarm için okunacak kısa metin ayrı saklanır. Yalnız `sirket_aciklamasi` için isteğe bağlı `reviewed=ReviewedReading(event=..., significance=...)` verilebilir. İki alan da dolu ve toplamları en fazla 400 karakter olmalıdır. Editörün okuması alıntının yerini tutmaz; daha sonra hazırlandıysa kendi `known_at` değerinden önce gösterilmemelidir.

### Zaman ve düzeltme kuralları

- **Ayrı olaylar:** Sorgu varsayılan olarak yalnız `origin="kap_arsiv"` sürümlerini okur. `sentetik` test verisiyle karışmaz. Yayın ve bilinirlik alarm anını aşamaz. Yayın penceresi 7 gündür (alt sınır dahil); `max_age=timedelta(hours=24)` gibi pozitif bir süre verilebilir. `valid_until` anı artık geçerli değildir. Uygun olaylar arasında en yeni yayın, eşitlikte büyük bildirim numarası kazanır.
- **Aynı bildirim numarası:** Gövdede doğrulanan sürüm normalde özetten güçlüdür; yalnızca bir detay dosyası bulunması özeti güçlendirmez. Eş güçte daha sonra bilinen sürüm geçerlidir. Eş güç ve aynı bilinirlik anında çelişki varsa ikisinden biri seçilmez. Güçlü sürüm bekletilmişse eski olumlu satış geri gelmez; özetteki açık fesih/koşul da önceki satış kanıtını geçersiz kılabilir.
- **Farklı numaralı düzeltme:** `supersedes` açık bir bağdır; başlık benzerliğinden çıkarılmaz. Yükleyici önceki **aynı kaynak ve hisseye** ait kaydı göstermeli, düzeltme doğrulanmış gövde kanıtı taşımalıdır. Geçerliyse önceki kayıt düzeltmenin bilindiği andan itibaren elenir; düzeltmenin süresi dolunca eski kayıt geri dönmez. Geçersiz bağda işlem geri alınır.

Bu kurallar gerçek ilk bilinirlik saatinin kendiliğinden bulunduğu veya her yanlış etiketin yakalandığı anlamına gelmez. [Bilinen sınırlar ve kabul ölçütleri](ANA-MANTIK.md#açık-kontroller) · [sorgu ve sürüm testleri](../tests/test_alarm_context.py).

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
