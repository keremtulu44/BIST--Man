# Veri kaynakları: hangi bilgi nereden gelir?

[Ana mantık](ANA-MANTIK.md) için **olayın kaynağı KAP**, alarmın kaynağı ise **ana sistemin grafiğidir**. BIST-Man bu iki kaynağı birbirinin yerine kullanmaz. Hazırlıkta gereken veri aşağıdadır; alarm geldiğinde bu uçların hiçbiri çağrılmaz.

| Girdi | Nereden / ne zaman? | Neye yeter, neye yetmez? |
|---|---|---|
| **Günün bildirim listesi** | `KapClient.light()` → `/tr/api/disclosure/list/light`. Demo aynı biçimdeki [kayıtlı listeyi](../tests/fixtures/kap/list_light_live.json) `parse_light` ile okur. | Bildirim no, yayın, unvan, konu, özet ile ön eleme yapılır. Bu kesitte yapısal hisse ve sınıf yoktur. Liste başlığı ihale sonucu, imza veya kesin bedel kanıtı değildir. |
| **Bildirim detayı** | `KapClient.detail(index)` → `/tr/api/notification/attachment-detail/{index}`. Örnek: [1699030 detayı](../tests/fixtures/kap/detail_1699030_live.json). | Yapısal kod (`stockCode`, yoksa `relatedStocks`), sınıf ve Türkçe gövde ile aday doğrulanabilir. Başka no/zaman/şirketin gövdesi kullanılamaz. Alıntı ve kaynak bağı burada önemlidir; alarm isteğine tüm HTML veya ekler taşınmaz. |
| **Şirket kimliği** | Yerel [unvan → hisse eşlemesi](../src/bistman/ticker_map.py); ayrıca [KAP şirket listesi istemcisi](../src/bistman/sources/kap_companies.py) geçmiş sorgusu için kod → üye OID sağlar. | Bu iki eşleme aynı şey değildir. OID bir üyeyi sorgulamak içindir; her satırın o şirketi anlattığını kanıtlamaz. Birden çok hisseye ait olayı rastgele birine bağlama. |
| **Üye geçmişi** | `/tr/bildirim-sorgu-sonuc?member={oid}` → [kayıtlı MTRKS geçmiş tablosu](../tests/fixtures/kap/member_history_mtrks.html). | Yeniden oynatma ve kapsam denetimi için geçmiş satırları verir. Tablonun bugün indirilebilmesi, gövdenin geçmişte hangi saatte sisteme girdiğini göstermez. Tüm geçmiş, alarm cevabına eklenmez. |
| **Mali ölçek** | Depoda [İş Yatırım `MaliTablo` istemcisi](../src/bistman/sources/isyatirim_client.py) ve [TTM hesabı](../src/bistman/analysis/ttm.py) var. Paketleyiciye, varsa, yayın zamanı ve kaynağı belirli `PublishedRow` sürümleri verilir. | İmzalı TL satış bedeli için aynı şirketin, **haber anına kadar yayımlanmış** karşılaştırılabilir sanayi cirosu kullanılabilir. Dönem sonu tek başına yayın zamanı değildir. Banka faiz gelirini sanayi cirosu gibi, kümülatif sütunları da bağımsız çeyrekler gibi kullanma. Kaynak veya güvenilir payda yoksa oran yok. |
| **Alarm girdisi** | Ana sistem `hisse` ve saat dilimli `alarm_zamani` verir. | Tek yerel SQLite okuması içindir. Mum, fiyat, hacim, portföy, grafik görseli veya yorum API’si anahtarı gerekmez. |

## Hazırlıkta uygulanacak sınır

Canlı toplama bağlandığında liste **ortak** okunmalı; her hisse veya her alarm için aynı liste yeniden çekilmemeli. İlk elemeden geçen yeni/değişmiş aday için kimlik, sonuç veya kanıt eksikse ilgili no ile detay kontrol edilmeli. `KapClient` taşıma hatalarını, 429 ve 5xx yanıtlarını sınırlı sayıda tekrar dener; diğer HTTP hataları tekrar denenmez. Bozuk liste satırları [ayrıştırma sonucunda](../src/bistman/sources/base.py) görünür. Başarılı bir HTTP isteği, bütün adayların detayının incelendiği anlamına gelmez.

**Bugünkü yükleyici** [dört seçilmiş gerçek kaydı](../scripts/alarm_baglami.py) dosyalardan hazırlar. MTRKS örneği kodu zaten içeren detay kaydıyla başlar; kodu olmayan liste satırından o detayı otomatik keşfettiğini göstermez. Canlı tarama zamanlayıcısı, kaldığı yerden sürdürülen bir toplama imleci ve “şu ana kadar tarandı” kaydı yoktur. `kap_arsiv` / `sentetik` ayrımını yükleyici belirtir; URL’nin görünüşünden güven üretilmez. Varsayılan alarm sorgusu `kap_arsiv` okur. Yayın, ilk kullanılabilir an, düzeltme ve eksik veri riskleri [ana belgede](ANA-MANTIK.md) izlenir.

## Bu sorgunun kaynağı olmayanlar

- **Fiyat/endeks serileri ve emtia:** Eski skor ve fiyat tepkisi deneylerine aittir. Grafik sinyali üretmek veya en son gelişmeyi seçmek için kullanılmaz.
- **Kur:** Alarm paketini hazırlayan yolda tarihli kur kaynağı yoktur. EUR/USD bedelini özgün biriminde bırakmak, bugünün kuruyla geçmiş bir satışa TL oranı vermekten doğrudur.
- **borsapy, MKK API Portal, TCMB/EVDS:** [Önceki kütüphane deneyi](VERI-KATMANI-BORSAPY.md) ve [kaynak seçenekleri araştırmasında](arsiv/KAYNAK-ARASTIRMASI.md) geçer. Alarm demosu için kurulum, abonelik veya anahtar şartı değildir. Desteklenen Python sürümü ve bağımlılıklar [pyproject.toml](../pyproject.toml) içindedir. Kamuya açık erişim yeniden dağıtım izni anlamına gelmez.
