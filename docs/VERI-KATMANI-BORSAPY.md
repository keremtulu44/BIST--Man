# VERİ KATMANI: borsapy (kişisel/eğitim kullanımı)

> Karar: veri erişimi **borsapy** üzerinden. Amaç kişisel/eğitim/deneyim →
> MKK resmî API kaydı, BIST DataStore lisansı, VERDA sözleşmesi **gerekmiyor**.
> `borsapy` lisans metni kişisel/eğitim kullanımına izin veriyor.
>
> Doğrulama yöntemi: `borsapy==0.11.0` wheel'i indirilip **kaynak kodu okundu**.
> Aşağıdaki her satır koddan çıkarıldı, dokümandan değil.

---


## ⚠️ Doğrulama bulguları (17.11.2026 — kullanıcının makinesi, gerçek veri)

Aşağıdakiler bu dokümanın yukarıdaki bölümlerini **geçersiz kılar**. Gerçek
çalıştırma raporu: `docs/dogrulama-sonuc.json`.

| Alan | Beklenen | Gerçek | Sonuç |
|---|---|---|---|
| `ttm_income_stmt` | TTM | 4 çeyrek sütununun toplamı; tablo **kümülatif** olduğu için **2x fazla** (THYAO 2.72 trilyon vs ~1.36 trilyon) | Kullanılmıyor. `analysis/ttm.py` kendi hesabını yapıyor |
| Kalem adı eşleşmesi | `faiz gelirleri` yeter | AKBNK'ta `1.7 Diğer Faiz Gelirleri` (alt kalem) eşleşti | `select_row` üst kalem (Roma rakamlı) tercih ediyor |
| `fast_info` | PD, F/K, fiyat | **`{}` boş** (iki hissed de) | `no_market_cap` bayrağı; fiyat `history()`'den alınacak |
| Çeyreklik sütun biçimi | tekil çeyrek | `2026Q2` = ilk 6 ay (YTD) | TTM = FY + YTD − YTD(geçen yıl) |

**Ders:** borsapy TTM'i kendi içinde tutarlıydı (TTM == 4 sütun toplamı, fark
%0.0000). Tutarlılık doğruluk değil — yıllık tabloyla karşılaştırmadan
"doğrulandı" denmez.

## 1. Tek cümlelik sonuç

**borsapy iyi bir "detay" katmanı, kötü bir "firehose".**
Piyasa geneli bildirim akışını vermiyor (sadece hisse bazlı, son 20).
O yüzden mimari ikiye ayrılıyor:

```
FIREHOSE (bizim, ~40 satır)        DETAY (borsapy)
KAP list ucu ──► disclosure_id ──► hisse finansalları (TTM, banka UFRS)
tüm piyasa, cursor'lı              fiyat/PD/FK, kur, enflasyon, unvan eşleme
```

---

## 2. Ne veriyor (doğrulanmış)

| İhtiyaç | borsapy API | Kaynak | Not |
|---|---|---|---|
| Şirket evreni (ticker+unvan+şehir) | `companies()` / `search_companies()` / `Ticker._get_kap().get_companies()` | KAP Excel `api/company/generic/excel/IGS/A` | 24 saat cache |
| Bildirim listesi (hisse bazlı) | `Ticker.news` → `DataFrame[Date, Title, URL]` | KAP `bildirim-sorgu-sonuc?member=<OID>` | **sadece son 20, piyasa geneli yok** |
| Bildirim gövdesi | `Ticker.get_news_content(disclosure_index)` | KAP Bildirim sayfası | ham HTML döner |
| Beklenen bildirim takvimi | `Ticker.calendar` | KAP expected-disclosure | bilanço sezonu tahmini için |
| Finansal tablolar | `get_income_stmt/get_balance_sheet(financial_group=, quarterly=, last_n=)` | İş Yatırım `MaliTablo` | `XI_29` / `UFRS` / `UFRS_K` |
| TTM | `ttm_income_stmt`, `get_ttm_income_stmt()` | aynı | son 4 çeyreği toplar (bkz. §4.3) |
| Anlık fiyat | `fast_info` / `info` | TradingView (+İş Yatırım) | varsayılan ~15 dk gecikmeli |
| Gün sonu fiyat geçmişi | `history(period=, interval=)` | TradingView WS | split/temettü düzeltmeli |
| Çarpanlar | `Ticker._get_isyatirim().get_company_metrics(sym)` | İş Yatırım | F/K, PD vb. — `source` ile sakla |
| Temettü / sermaye artırımı | `dividends()`, `Ticker._get_isyatirim().get_capital_increases(sym)` | İş Yatırım | pay sayısı değişimi için kritik |
| Kur | `FX("USD").current`, `history()` | canlidoviz/doviz.com | TCMB değil → doğrulama için `TCMB()` |
| Enflasyon / EVDS | `Inflation().latest()`, `evds_series("TP.FG.J0", ...)` | TÜİK/TCMB | EVDS key ücretsiz (`evds3.tcmb.gov.tr`) |
| Ortaklık yapısı | `major_holders` | İş Yatırım | halka açıklık |

Ayrıca paket içinde `ReplaySession`, `create_replay`, `TradingViewStream`,
`Backtest`, `Screener` var — **bunlar fiyat/bar replay'i**, bizim
"verdict replay"imiz değil. Karıştırma; bizim replay `llm/` + `replay_*`
şemasında, ayrı.

---

## 3. Ne vermiyor (bunları biz yazacağız)

| Eksik | Sonuç |
|---|---|
| Piyasa geneli bildirim akışı | Firehose bizim: KAP list ucu (veya `byCriteria`), 15-60 sn poll |
| Kalıcı/tekil bildirim kimliği | `URL`'den `disclosureIndex` parse edilir (`/Bildirim/1530826`) — stabil, ama biz yine de doğrulayacağız |
| Dedup / idempotency | DB unique constraint (borsapy'de böyle bir kavram yok) |
| TMS-29 enflasyon vintage bilgisi | Bizim kontrolümüz (§4.3) |
| Banka/endüstri ayrımı | `financial_group` parametresini **biz** doğru geçeceğiz (`company.statement_type`) |
| Rate limit / backoff | borsapy throttle etmiyor → limiter bizim |
| Async | **paket tamamen senkron** → §4.1 |

---

## 4. Dört tuzak (koddan çıktı, hepsi sessiz)

### 4.1 Paket tamamen senkron — asyncio'ya köprü şart

Kodda tek bir `async def` yok; tüm provider'lar `httpx.Client` kullanıyor.
Bizim poller/worker'lar asyncio. Köprü:

```python
# sources/isyatirim_labels.py
_LIMITER = asyncio.Semaphore(4)          # eşzamanlı dış istek tavanı

async def _call(fn, /, *a, **kw):
    async with _LIMITER:
        return await asyncio.to_thread(fn, *a, **kw)
```
- Neden `to_thread`: senkron HTTP'yi event loop'ta çağırırsan **tüm poller durur**.
- Neden 4: İş Yatırım + KAP'a aynı anda 40 istek = ban/429. Darboğaz zaten LLM.
- Tek bir global `httpx.AsyncClient` yazma hayaline kapılma; borsapy kendi
  client'ını yönetiyor. Köprü en temiz çözüm.

### 4.2 `@cached_property` — uzun ömürlü `Ticker` nesnesi bayatlar

`news`, `calendar`, `balance_sheet`, `quarterly_income_stmt`,
`ttm_income_stmt`, `major_holders` **`cached_property`**. Yani:

```python
t = bp.Ticker("THYAO")
t.news          # ilk çağrı
t.news          # 3 saat sonra → AYNI eski DataFrame (sessizce bayat)
```

Daemon'da `Ticker` nesnesini elde tutarsan haber akışı **sessizce donar**.
Çözüm: **her işlemde yeni `Ticker`**, tutma. Maliyeti yok çünkü provider'lar
modül seviyesinde singleton (`get_kap_provider()`) — nesne yaratmak ucuz,
bağlantı havuzu paylaşılıyor.

Yedek: listener yolunda `.news` yerine `_get_kap().get_disclosures(sym)`
çağır (private ama cache'siz). Tercih: yeni `Ticker`.

### 4.3 TTM "son 4 çeyreği topla" — doğrulama bizde

```python
# borsapy'den birebir:
last_4_quarters = quarterly_df.iloc[:, :4]
return numeric_df.sum(axis=1).to_frame(name="TTM")
```

Sorunlar:
- Çeyrek **sürekliliği** kontrol edilmiyor (bir çeyrek eksikse 3 çeyrek toplanır).
- **TMS-29 vintage** bilinmiyor → 2023 enflasyon düzeltmesi öncesi/sonrası
  çeyrekler karışabilir.
- Banka için hangi kalemlerin toplandığı şablon değişince sessizce değişir.

Bunu borsapy'ye bırakmıyoruz. `features.py` içinde:
```python
quarters = df.columns[:4]
if not _is_consecutive(quarters):            # 2025Q3,Q2,Q1,2024Q4
    flags.append("ttm_gap"); confidence *= 0.6
if len({_vintage(q) for q in quarters}) > 1:  # 2023-01 öncesi/sonrası
    flags.append("mixed_restatement_vintage"); confidence *= 0.6
if statement_type == "bank" and "Satış Gelirleri" in df.index:
    flags.append("bank_template_mismatch"); confidence *= 0.5
```

### 4.4 Boş `DataFrame` = hata değil (sessiz boşluk)

`get_disclosures` KAP sayfasındaki Next.js gömülü JSON'u **regex ile** çekiyor:

```python
pattern = r'publishDate\\":\\"([^\\"]+)\\".*?disclosureIndex\\":(\d+).*?title\\":\\"([^\\"]+)\\"'
matches = re.findall(pattern, response.text, re.DOTALL)
...
return pd.DataFrame(records)          # matches boşsa: BOŞ DF, exception YOK
```

KAP Next.js sürümünü değiştirdiğinde sistem hata vermez — "bugün haber yok"
der ve haftalarca ölü kalır. Bu yüzden adapter'da tek kural:

> **Boş sonuç = şüpheli.** Firehose o hisse için bildirim gördüyse ama
> borsapy boş döndüyse → `parse_anomaly` + alarm. Boşluğu "haber yok" diye
> yorumlayan kod yazma.

---

## 5. Sürüm ve tedarik zinciri

`borsapy` **0.11.0**, PyPI'da `Development Status :: 3 - Alpha`, tek geliştirici.
Kişisel proje için sorun değil, ama:

1. **Tam sürüm pinle:** `borsapy==0.11.0` (tilde değil, eşittir).
2. `pip install --require-hashes` (requirements + hash).
3. Otomatik güncelleme yok (`dependabot` kapat). Upgradeler **elle**:
   yeni sürüm → fixture testleri → replay karşılaştırması (§6).
4. Tüm borsapy çağrıları `sources/isyatirim_labels.py` içinde. Başka dosyada
   `import borsapy` yok (`grep` ile denetlenebilir kural). Paket bozulursa ya
   da lisans değişirse **tek dosya** değişir.
5. `httpx.Client` sızıntısı: kendi `Ticker` nesneni yaratıp tutmuyorsan sorun
   yok; provider'lar singleton, süreç sonunda kapanıyor.

---

## 6. Fixture disiplini (bu pakete güvenmenin tek yolu)

borsapy'nin ham çıktısını değil, **çektiği kaynağın ham yanıtını** kaydet:

```
tests/fixtures/
├── kap/bildirim_sorgu_thyao.html          # borsapy'nin parse ettiği HTML
├── kap/companies_igs_a.xlsx
├── isyatirim/malitablo_thyao_xi29.json
├── isyatirim/malitablo_akbnk_ufrs.json
├── tcmb/today.xml
└── borsapy/                               # borsapy'nin döndürdüğü DataFrame (parquet/csv)
```

Testler iki katmanlı:
1. **Kaynak testi:** ham fixture → bizim parser → beklenen alanlar.
2. **Sözleşme testi:** borsapy sürümü değişince aynı fixture'dan aynı çıktı
   geliyor mu? (borsapy'yi mock'lamıyoruz; **gerçekten** çağırıp karşılaştırıyoruz,
   yoksa "borsapy değişti" haberini ancak üretimde alırız.)

---

## 7. Revize dosya yapısı

```
src/bistman/
├── sources/
│   ├── base.py                # Protocol'ler (DisclosureSource, FundamentalsSource, ...)
│   ├── isyatirim_labels.py         # TEK borsapy temas noktası + to_thread köprüsü + limiter
│   ├── kap_firehose.py        # piyasa geneli bildirim akışı (borsapy'de YOK)
│   └── fixtures_src.py        # test için sabit veri
├── features.py                # TTM doğrulama, oranlar, cezalar (§4.3)
└── ...
```

`isyatirim_labels.py` içindeki arayüz (~imza):

```python
class BorsapyFundamentals:
    async def snapshot(self, ticker: str, statement_type: str) -> FundamentalRow | None:
        """TTM + son bilanço. statement_type='bank' → financial_group='UFRS'."""
    async def market(self, ticker: str) -> MarketRow | None:
        """fiyat, PD, F/K; source='tradingview'|'isyatirim' alanıyla."""

class BorsapyCompanies:
    async def universe(self) -> list[Company]        # ticker, unvan, şehir

class BorsapyDisclosures:                            # sadece detay, firehose değil
    async def recent(self, ticker: str, limit: int = 20) -> list[DisclosureStub]
    async def content(self, disclosure_index: int) -> str
```

---

## 8. Revize faz planı

| Faz | İçerik | Bitiş kriteri |
|---|---|---|
| **0** | `money.py` (hazır ✅) + `features.py` TTM doğrulama + testler | 40+ örnek, ağ yok |
| **1** | `isyatirim_labels.py` + fixture kaydı: 3 hisse × (TTM, bilanço, fiyat) | Fixture'lar var, 3 hisse sayıları elle doğrulandı |
| **2** | `kap_firehose.py` + `disclosure` tablosu + dedup | 24 saat kesintisiz, 0 duplike |
| **3** | Features (firehose metni + borsapy finansalları) + kural skor + Telegram | Uçtan uca bildirim, LLM'siz |
| **4** | LLM + replay + eşik kalibrasyonu | Golden set ile ölçülmüş |

Faz 1'de **elle doğrulama** şart: borsapy'nin THYAO TTM cirosu ile KAP'taki
finansal rapor bildirimi aynı mı? 3 hisse için gözle karşılaştır, sayı tutmuyorsa
yönü (borsapy'yi mi, kendi hesabımızı mı kullanacağız) o zaman netleşir.

---

## 9. Riskler ve karşılıkları

| Risk | Olasılık | Karşılık |
|---|---|---|
| KAP Next.js değişir, `news` boş döner | Orta | Firehose çapraz kontrolü + boşluk alarmı (§4.4) |
| İş Yatırım şablonu değişir | Orta | Fixture testi; `MaliTablo` bizim de çağırabildiğimiz tek endpoint |
| borsapy bakımsız kalır | Düşük-orta | Tek dosyada izole; kendi `MaliTablo` istemcimiz 60 satır |
| TradingView fiyat WS'i engeller | Düşük | Gün sonu F/K için İş Yatırım yeterli; fiyatı günlük de çekebiliriz |
| Rate limit (İş Yatırım/KAP) | Orta | Semaphore(4) + 429/`Retry-After` + adaptif poll |
| Kişisel kullanım sınırı aşılır (ürünleşme) | — | O gün `sources/` değişir; mimari aynı kalır |

---

## 10. Karara bağlananlar (decision log güncellemesi)

- **D5 →** Finansal tablolar: `borsapy` (İş Yatırım `MaliTablo`) + bizim TTM doğrulaması.
- **D6 →** Bildirim akışı: **kendi `kap_firehose`** (borsapy'de piyasa geneli feed yok); detay/HTML `borsapy`.
- **D9 →** Lisans/ resmiyet: gerekmiyor, kişisel/eğitim kullanımı.
- **Yeni D12 →** borsapy tam sürüm pinli, tek dosyada izole, otomatik upgrade yok.
- **Yeni D13 →** borsapy çağrıları `asyncio.to_thread` + `Semaphore(4)` ile köprülenir.
- **Yeni D14 →** `Ticker` nesnesi asla uzun ömürlü tutulmaz (`cached_property` bayatlar).
