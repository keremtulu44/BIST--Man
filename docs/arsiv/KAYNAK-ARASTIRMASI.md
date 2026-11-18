# Arşiv: veri sağlayıcısı seçeneklerinin ilk karşılaştırması

> **Tarihsel kaynak araştırması.** Aşağıdaki MKK, fiyat, kur, kota ve lisans notları ilk taslağın girdileriydi. Endpoint ve ücretler bu incelemede yeniden doğrulanmadı; bu sayfa güncel erişim, izin veya kurulum talimatı değildir. Bugünkü veri yolu: [Veri kaynakları](../VERI-KAYNAKLARI.md).

## 1. MKK API Portal — resmî, ücretsiz, key'li

**Ne:** Merkezi Kayıt Kuruluşu'nun (KAP'ı işleten kurum) API geçidi.
13.01.2025'te açıldı. KAP veri yayın servisleri buraya entegre edildi ve
**uygulama ücretsiz**.

**Nasıl erişilir:**
1. `https://apiportal.mkk.com.tr/` → hesap aç
2. Hesap onaylanır
3. Uygulama oluştur → **API Key** al
4. Kullanmak istediğin API ürününe kayıt ol
5. Test ortamı: `apigwdev.mkk.com.tr` · Üretim: `apigw.mkk.com.tr`

**Servis yolları** (bir Python istemcisinin kodundan okundu — kendi
kaydını yaptıktan sonra **ürün dokümanından teyit et**):

```
GET /api/vyk/disclosures            # bildirim listesi
GET /api/vyk/lastDisclosureIndex    # son bildirim indeksi  ← cursor için altın
GET /api/vyk/members                # şirket/üye evreni (ticker + unvan)
GET /api/vyk/memberSecurities       # pay sayısı / kote bilgisi
GET /api/vyk/blockedDisclosures     # erişime kapalı bildirimler
GET /api/vyk/caEventStatus          # hak kullanım süreç durumu
GET /api/vyk/funds                  # fon bildirimleri
Auth: `Authorization: Basic <apiKey:apiSecret>` → sonra `Bearer <token>`
Token: `/auth/generateToken?apiKey=...`
```

**Neden bu bizim için ideal:**
- `lastDisclosureIndex` → **monoton cursor**. `MIMARI.md §2`'deki tasarımın
  tam istediği şey. "Son gördüğüm set" derdi yok.
- Bildirim listesi yapısal: `disclosureIndex`, `disclosureId`, `stockCode`,
  `relatedStocks`, `publishDate`, `disclosureClass`, `summary`.
  → **Ticker regex'i tamamen gereksiz**, KAP zaten `relatedStocks` veriyor.
- Ekler (`attachments[]`) ve gövde (`disclosureBody[]`) ayrı alanlar.

**Bilinmeyen / risk:**
- **Rate limit ve kotayı bilmiyoruz** — kayıt sonrası ürün dokümanında yazar.
  Tasarımı buna göre savunmacı yap: adaptif poll (15 sn) ilk günden fazla olabilir.
- Onay süresi kişiden kişiye değişebilir. **Bu, geliştirmeyi bloklamamalı** (§5).
- Kota ticari kullanımda değişebilir; ürün şartlarını kayıt sırasında oku.

**Kaynak:** MKK duyurusu "MKK API Portal Yayında" (13.01.2025), MKK 2025
faaliyet raporu, `apiportal.mkk.com.tr`. Servis yolları: PyPI `pykapfinance`
0.1.1 `client.py` kaynak kodu.

> ⚠️ O paketin içinde **gömülü bir varsayılan API key** var. Onu kullanma:
> başkasının kotası, habersiz iptal edilebilir, ToS ihlali. Kendi key'ini al.

---

## 2. KAP iç API — yedek plan (key'siz)

Resmî API değil; KAP arayüzünün kendi çağırdığı uçlar. Kayıt/onay beklemeden
**bugün** çalışır, bu yüzden geliştirme sırasında altın değerinde.

```
POST https://www.kap.org.tr/tr/api/disclosure/members/byCriteria
     Content-Type: application/json
     Referer: https://www.kap.org.tr/tr/bildirim-sorgu
     User-Agent: <kendini tanıtan bir UA>            ← zorunlu, yoksa WAF yer
GET  https://www.kap.org.tr/tr/api/disclosure/list/light      # günün tüm bildirimleri
GET  https://www.kap.org.tr/tr/api/notification/attachment-detail/{disclosureIndex}
GET  https://www.kap.org.tr/tr/Bildirim/{disclosureIndex}     # detay HTML
```

**Bilinen davranışlar (kaynak: `caganco/trailingedge` KAP endpoint notları + `MuhammedIsildak/kap-notifier`):**
- Oturum ısıtma: önce `GET /tr/bildirim-sorgu` → çerez al, sonra API.
- Cevaplarda **pagination token yok** → pencere dar tutulmalı; 2.000 sonuç
  dönerse tarih aralığını böl.
- 7 günlük pencerelerde 429 görülmemiş — ama bu "sınırsız" demek değil.
- Tarih sorgusu bot korumasına düşebilir (**HTTP 666**).
- `list/light` **sadece bugünü** döndürür → bir gün çalışmazsan o günün
  bildirimleri **bu liste ucundan** geriye dönük alınamaz (üye geçmişi ayrı bir uçtur). Boşluk dedektörü şart (`MIMARI.md §2`).

**O dönemin önerisi:** MKK birincil, KAP yedek olarak düşünülmüştü; böyle bir geçiş mekanizması uygulanmadı.

---

## 3. Finansal tablolar

### 3.a MKK / KAP finansal rapor bildirimleri (otoriter)

Finansal tablolar KAP'a **bildirim** olarak düşer; gövde HTML tablo
(`disclosureBody`), ekte PDF/XBRL. Yapısal kalem kodları (XI_29 sanayi,
UFRS banka) buradan gelir. MKK ürünü varsa bu katman birincil.

### 3.b İş Yatırım `MaliTablo` — hazır ve hızlı (doğrulandı)

```
GET https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo
  companyCode=THYAO
  exchange=TRY
  financialGroup=XI_29 | UFRS | UFRS_K
  year1=2025 & period1=3 & year2=.. & period2=..
```
Doğrulama: `isyatirimhisse` 5.0.1 kaynak kodunda bu URL + parametreler birebir
okundu. Aynı paket `HisseTekil` ile fiyat, `IndexHistoricalAll` ile endeks veriyor.

**Kalem eşlemesi kritik:**
| `financialGroup` | Kim için |
|---|---|
| `XI_29` | Sınai/ticari şirketler (varsayılan) |
| `UFRS` | Bankalar |
| `UFRS_K` | Konsolide olmayan / özel durumlar |

Banka için `XI_29` çekersen alanlar boş/yanlış gelir → `MIMARI.md §2.4-d`.
`period1` değerleri 3/6/9/12 = çeyrek sonları. TTM = son 4 çeyrek toplamı
(**aynı dönem sonu bazında**).

### 3.c `borsapy` — hazır ama lisansı ticari değil

`borsapy` 0.11.0: `Ticker.balance_sheet`, `quarterly_income_stmt`,
`ttm_income_stmt`, bankalar için `financial_group="UFRS"`, ayrıca `FX`,
`Inflation`, `EVDS`, `replay`, `backtest` modülleri var. Kaynakları:
İş Yatırım + KAP + TCMB + TradingView (fiyat).

> **Lisans uyarısı (paketin kendi metni):** "yalnızca kişisel kullanım ve
> eğitim amaçlıdır… ticari amaçla kullanılamaz. Ticari kullanım için Borsa
> İstanbul ile iletişime geçin." Kişisel kullanımda sorun yok; ürünleştirirsen
> bu katmanı lisanslı kaynakla değiştirmen gerekir. **Mimari sonucu:** veri
> erişimini `Source` arayüzünün arkasında tut (zaten öyle), böylece lisans
> gerektiğinde tek dosya değişir.

---

## 4. Fiyat, kur, enflasyon

| Veri | Kaynak | Erişim | Not |
|---|---|---|---|
| Gün sonu fiyat/PD | İş Yatırım `HisseTekil` | key yok | Günlük 1 istek/hisse yeter |
| Gün sonu fiyat (resmî) | BIST **DataStore** | ücretli, dosya | EOD fiyat+hacim ~50 TL/ay; kurumsal sözleşme |
| Fiyat (resmî, API) | BIST **VERDA** | kurumsal | Self-servis key yok |
| Fiyat (3. taraf, lisanslı) | `api.istfin.com` | 399 TL/ay | REST + MCP; lisans iddiası doğrulanmalı |
| Kur (bugün) | `tcmb.gov.tr/kurlar/today.xml` | key yok | Basit XML, günlük cache |
| Kur + TÜFE (seri) | TCMB **EVDS** `evds3.tcmb.gov.tr` | ücretsiz key | `TP.DK.USD.A.YTL`, `TP.FG.J0` |
| Enflasyon | TÜİK / EVDS | key | TMS-29 vintage kontrolü için |

**F/K'yı satın alma, hesapla.** Sağlayıcılar "ana ortaklık payı mı, sürdürülen
faaliyet mi" konusunda tutarsız. `PD / net_income_ttm(ana ortaklık)` en temizi.

---

## 5. Araştırma sırasında önerilen, uygulanmamış tasarım

1. **Resmî API var ama onay süreci var** → geliştirme MKK onayına bağlı
   olmamalı. `DisclosureSource` arayüzü + 3 implementasyon:
   `MkkApiSource` (üretim), `KapHtmlSource` (yedek/geliştirme), `FixtureSource` (test).
   Aynısı finansallar için: `MkkFinancialsSource` / `IsyatirimSource` / `CsvSource`.
2. **`relatedStocks` yapısal geliyor** → ticker regex'ine olan ihtiyaç kalkıyor.
   Bu, `MIMARI.md §3.3`'teki 4 katmanlı çözümü 2 katmana indirir (fallback
   sadece eski/yapısal olmayan metinler için kalır).
3. **`lastDisclosureIndex` var** → `seen_ids` seti tasarımı tamamen çöpe.
   Cursor + `ON CONFLICT DO NOTHING`.
4. **Önceki günler `list/light` üzerinden geri doldurulamaz** → boşluk dedektörü opsiyonel değil.
5. **Ücretsiz katmanların lisansı kişisel** → ürünleştirme kararı verilirse
   veri katmanı BIST DataStore / VERDA / lisanslı sağlayıcıya taşınır. Kod
   tarafında değişen tek şey `sources/` klasörü.
