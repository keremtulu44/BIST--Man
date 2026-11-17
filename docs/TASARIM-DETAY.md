# TASARIM-DETAY — Karar verdirici ayrıntılar

> Bu dosya `MIMARI.md`'nin devamı. Orada "nasıl durur" yazdı, burada **"hangi
> seçeneği neden seçiyoruz"** var. Her bölüm sonunda karar ve gerekçesi.
> Kural: buradaki bir karar değişirse, kod değil **önce bu dosya** değişir.

---

## 1. Açık karar kaydı (decision log)

| # | Karar | Seçim | Durum |
|---|---|---|---|
| D1 | Dil/stack | Python 3.12, asyncio, httpx, pydantic v2 | ✅ kesin |
| D12 | Veri kütüphanesi | `borsapy==0.11.0` pinli, tek dosyada izole | ✅ kesin |
| D13 | borsapy ↔ asyncio | `asyncio.to_thread` + `Semaphore(4)` | ✅ kesin |
| D14 | `Ticker` ömrü | uzun ömürlü tutulmaz (`cached_property` bayatlar) | ✅ kesin |
| D2 | Kuyruk | Postgres `FOR UPDATE SKIP LOCKED` | ✅ kesin |
| D3 | Dedup | DB unique constraint (bellek değil) | ✅ kesin |
| D4 | Skor şeması | 3 eksen: `impact / sentiment / confidence` | ✅ kesin (§3) |
| D5 | Bilanço kaynağı | **borsapy** (İş Yatırım `MaliTablo`) + bizim TTM doğrulaması | ✅ kesin (VERI-KATMANI-BORSAPY) |
| D6 | Bildirim akışı kaynağı | **Kendi `kap_firehose`** (borsapy'de piyasa geneli feed yok) → [VERI-KATMANI-BORSAPY.md](VERI-KATMANI-BORSAPY.md) §3 | ✅ kesin |
| D7 | Ciro bazı | TTM + aynı enflasyon düzeltme "vintage"ı | ✅ kesin (§2.3) |
| D8 | Bildirim politikası | 3 kademe (anında / günlük özet / sadece DB) | ✅ kesin (§3.4) |
| D9 | LLM sağlayıcı | Arayüz sabit, sağlayıcı değişken; seçim golden set ile | ⏳ eval bekliyor (§6) |
| D10 | Zaman | DB'de her şey UTC, sadece gösterimde TRT | ✅ kesin |
| D11 | Deploy | tek container + managed Postgres | ✅ kesin |
| D15 | Evren | BIST 50 + XU100/XBANK referansı + altın/gümüş/Brent | ✅ kesin (PLAN-V1 §1) |
| D16 | Eşik kalibrasyonu | 6 aylık bildirim + anormal getiri (event study) | ✅ kesin (PLAN-V1 §2) |
| D17 | Emtia | ayrı alarm akışı; hisse skoruyla aynı ölçekte DEĞİL | ✅ kesin (PLAN-V1 §1) |
| D18 | LLM | v1'de yok; Faz 7'de Gemini/kendi wrapper | ✅ kesin |
| D19 | Kanal | Telegram (v1) → web (Faz 6) | ✅ kesin |

---

## 2. Veri kaynağı: KAP mı, İş Yatırım mı?

### 2.1 İki farklı veri var, tek karar değil

Bunu tek soru gibi kurmak hatanın kaynağı. Aslında **iki ayrı akış** var:

| | Bildirim akışı (olay) | Bilanço/çarpan (durum) |
|---|---|---|
| Neyi verir | "X şirketi şu an şunu açıkladı" | "X şirketinin cirosu şu" |
| Tazelik | saniye/dakika kritik | gün/çeyrek kritik |
| Eksik olursa | **ürün ölür** (haberi kaçırdın) | skor bozulur, ürün yaşar |
| Kaynak | KAP — alternatifi yok | İş Yatırım / KAP / CSV |
| Doğruluk ihtiyacı | tam (id, zaman, metin) | %1-2 tolerans kabul |

**Sonuç:** Bildirim akışını KAP'tan almak tartışmasız. Tartışılan tek şey
bilanço kaynağı.

### 2.2 Bilanço kaynağı karşılaştırması

| Kriter | İş Yatırım | KAP |
|---|---|---|
| Format | yarı-yapısal JSON/HTML, kalem adları hazır | HTML tablo + PDF ek; özet bilanço disclosure içinde |
| Hız | toplu çekim, tek istekte çok dönem | bildirim başına ayrı istek |
| Kapsam | çoğu şirket, bazen geçmiş dönem eksiği | yasal olarak tam |
| Banka/endüstri şablonu | ayrı şablon, kalem adları farklı | aynı sorun |
| Kırılganlık | yüksek (dokümante değil, habersiz değişir) | yüksek (HTML şablonu + PDF) |
| Otorite | türev veri (hatalı olabilir) | birincil kaynak |
| Ekstra | hazır çarpanlar (F/K vb.) | yok, kendin hesaplarsın |

**Öneri (D5):** İş Yatırım ana kaynak, **çeyreklik** KAP özet bilanço ile
çapraz kontrol. Fark > %1 ise `source='kap'` kazanır ve alarm üretilir
(veri sağlayıcı sessizce bozulduğunda haberin olsun).

### 2.3 Spike: kararı 4 saatlik deneyle ver

Kararı sezgiyle vermeyelim. `scripts/spike_sources.py` (henüz yok, Faz 1'de):

```
Örneklem (kasten zor): THYAO, AKBNK, SISE, ASELS, + 1 küçük ölçekli
Her biri için: son 8 çeyrek, 12 kalem (Satış Gelirleri, Net Dönem Kârı,
Ana Ortaklık Payı, Özkaynak, Toplam Varlık, Net Borç, ...)
Karşılaştır: mutlak fark > %1 olan kalemleri listele
Kazanan: daha az sapma + daha az parse hatası
```

Çıktı bir tablo olur, karar D5 güncellenir. Bu deney ayrıca ilk
`tests/fixtures/` setini üretir — yani çöpe gitmez.

### 2.4 Sessiz tuzaklar (bunlar skoru bozar, hata vermez)

**a) TMS-29 / enflasyon muhasebesi.** 2023'ten itibaren finansallar dönem
sonu satın alma gücüne göre düzeltiliyor. Sonuç: **2022 cirosu ile 2025
cirosu aynı ölçekte değil.** TTM hesaplarken 4 çeyreğin **aynı düzeltme
vintage'ından** olması gerekir; yoksa oran 2 katına kadar sapar.

```python
# aynı vintage kontrolü
if len({q.restatement_vintage for q in quarters}) > 1:
    flags.append("mixed_restatement_vintage")
    confidence *= 0.6          # oran hesaplanır ama güvenilmez
```

**b) Ana ortaklık payı ≠ net dönem kârı.** Konsolide bilançoda "Net Dönem
Kârı" azınlık payını içerir, "Ana Ortaklık Payı" içermez. F/K ve PD/DD
**ana ortaklık payı** ile hesaplanır. Kalem eşlemesinde bu ayrım yoksa F/K
%10-30 yanlış çıkar.

**c) Pay sayısı bayat.** Bedelli/bedelsiz sermaye artırımı sonrası PD
hesabı eski pay sayısıyla yapılırsa 2x hata olur. Pay sayısı **aynı
`as_of`** tarihinden alınmalı, ayrı bir yerden değil.

**d) Banka şablonu.** "Satış Gelirleri" bankada yok/yetim kalem. Banka için
`faiz_geliri + net_ücret_komisyon` kullan; `statement_type='bank'` dalı
olmadan tüm banka oranları 10x yanlış.

**e) Düzeltme bildirimleri.** Aynı finansal rapor revize edilirse KAP'a yeni
bildirim düşer. "Son rapor" mantığı yerine `(period_end, fetched_at)` ile
append-only tut, en yenisini seç — böylece revizyon kaybolmaz.

---

## 3. Skor modeli: tek sayı neden yetmez

### 3.1 Üç eksen

```
impact      : 0-10   ne kadar BÜYÜK/önemli (oranlardan türetilir, kod hesaplar)
sentiment   : +/0/-  hangi YÖNDE (metinden çıkar, LLM/kural)
confidence  : 0-1    ne kadar EMİNİZ (parse kalitesi, FX, veri tazeliği)
```

Tek `score: 0-10` kullanırsan şu iki haber aynı sayıya düşer:
- "Devasa sözleşme ama tutar belirsiz, metin muğlak" → riskli
- "Orta büyüklükte sözleşme, tutar net, ciro biliniyor" → güvenli

İkisi de 7/10 olur, oysa yapılacak şey farklı: biri **uyarı olarak**
gönderilmeli, diğeri **sinyal olarak**.

Kullanıcıya giden karar: `impact * confidence >= eşik`.
`confidence` düşükse aynı `impact` bildirim üretmez → gürültü doğal olarak azalır.

### 3.2 İki katman + birleştirici

```
                    ┌───────────────┐
 features ─────────►│ KURAL KATMANI │──► impact_rule (deterministik, test edilebilir)
                    └───────────────┘
                    ┌───────────────┐
 metin + features ─►│  LLM KATMANI  │──► impact_llm, sentiment, gerekçe
                    └───────────────┘
                             │
                             ▼
        impact = w_r * impact_rule + w_l * impact_llm
        confidence = base * ∏(cezalar)
```

**Kural katmanı (LLM yok, %100 test edilebilir):**

```python
SUBJECT_PRIOR = {
    "sözleşme/ihale":     +,   # orana göre ölçeklenir
    "yatırım kararı":     +,
    "bedelli sermaye":    -,   # seyreltme, kısa vade negatif
    "bedelsiz sermaye":  ~0,   # teknik, nötr
    "kâr dağıtım":       +/0,  # verimlilik kadar
    "dava/vergi cezası":  -,
    "pay geri alım":      +,   # programa bağlı
    "genel kurul":        0,   # rutin → filtre
}
RATIO_BUCKETS = [(0, "etkisiz"), (1, "küçük"), (3, "orta"), (10, "büyük"), (inf, "devasa")]
```

`impact_rule = bucket_score(ratio_revenue|mcap|net_income) + subject_prior`
şeklinde **açık ağırlıklarla**, tek fonksiyonda. Ağırlıklar §3.5'teki
replay ile kalibre edilir; koda gömülü "sihirli sayı" olmaz (`config/scoring.toml`).

**LLM katmanı sadece şunlara bakar** (aritmetik yapmaz, tekrarlıyoruz):
- Bu sözleşme **tek seferlik mi, tekrarlayan mı**? (metinden)
- Tutar **toplam mı, yıllık mı**? ("3 yıla yayılmış" ifadesi oranı 3'e böler)
- Metin **muğlak mı** (rakam vermeden "önemli" diyor mu)?
- Gerekçe cümlesi (kullanıcıya gösterilecek 1-2 satır)

### 3.3 Emniyet cezaları (confidence)

```python
penalties = {
    "no_amount":               0.45,
    "fx_fallback":             0.70,
    "mixed_restatement_vintage": 0.60,
    "bank_template_mismatch":  0.50,
    "implausible_ratio":       0.25,   # oran > %100
    "llm_low_confidence":      0.80,
    "stale_fundamentals":      0.85,   # > 400 gün eski
}
confidence = clamp(base * prod(penalties.values()), 0, 1)
```
Çarpımsal: iki ayrı sorun varsa ceza katlanır (toplamsal olsa "orta" derdi).

### 3.4 Bildirim politikası (D8)

| Koşul | Aksiyon |
|---|---|
| `impact * confidence >= 6.5` | anında push (60 sn içinde) |
| `3.5 <= ... < 6.5` | 18:15 günlük özetine ekle |
| `< 3.5` | sadece DB (sessiz) |
| `confidence < 0.4` ve `impact >= 6` | "belirsiz ama büyük" uyarı etiketiyle push |
| rutin `subject` ve tutar yok | LLM'e hiç gitmeden `filtered` |

Son satır önemli: **büyük ama emin olunamayan** haberi saklamıyoruz, etiketiyle
gönderiyoruz. Sessizce düşürmek, gürültüden daha pahalı bir hata.

### 3.5 Eşikler nereden gelir

Sezgiyle değil, **geçmiş üzerinde replay** ile (§6). İlk sürümde eşikler
geçicidir ve `config/scoring.toml`'da durur; kod değişmeden ayarlanabilir
olması şart (aksi hâlde her ayar bir deploy olur).

---

## 4. Pipeline state machine (asıl karmaşıklık burada)

### 4.1 Çok hisseli bildirim: gerçek dünyada sık

Birleşme/devralma, grup şirketi işlemi, sektör haberi → **bir bildirim, N
hisse**. Bu yüzden durum `disclosure` üstünde değil, `(disclosure, ticker)`
çiftinde tutulur:

```sql
create table disclosure_ticker (
  disclosure_id  text not null references disclosure(id) on delete cascade,
  ticker         text not null references company(ticker),
  status         text not null default 'new'
                 check (status in ('new','filtered','processing','scored',
                                   'scored_degraded','notified','failed','dead')),
  attempts       smallint not null default 0,
  locked_by      text,
  locked_at      timestamptz,
  features       jsonb,
  verdict        jsonb,
  last_error     text,
  primary key (disclosure_id, ticker)
);
create index on disclosure_ticker (status, disclosure_id)
  where status in ('new','processing');
```

**Neden bu ayrım kritik:** THYAO skorlanmış, AKBNK LLM timeout'a düşmüş
olabilir. `disclosure` üstünde tek `status` tutarsan bir hisse için tekrar
denemek zorunda kalır ve zaten bildirilmiş hisseyi ikinci kez bildirirsin.

### 4.2 Atomik iş alma

```sql
with cte as (
  select disclosure_id, ticker
  from disclosure_ticker
  where status = 'new'
     or (status = 'failed' and next_attempt_at <= now())
  order by disclosure_id
  limit $1
  for update skip locked
)
update disclosure_ticker t
set status = 'processing', attempts = t.attempts + 1,
    locked_by = $worker_id, locked_at = now()
from cte
where t.disclosure_id = cte.disclosure_id and t.ticker = cte.ticker
returning t.*;
```

### 4.3 Ölü worker temizleyicisi (reaper)

Worker LLM beklerken `SIGKILL` yerse satır `processing`de kalır ve **sonsuza
kadar** orada durur. Her naive implementasyonun kaçırdığı nokta:

```sql
update disclosure_ticker
set status = 'failed', last_error = 'stuck_reaper',
    next_attempt_at = now()
where status = 'processing'
  and locked_at < now() - interval '5 minutes';
```

`attempts >= MAX` ise `dead` + alarm. Reaper dakikada bir çalışır, tek SQL.

### 4.4 Geçiş kuralları (kim neyi yazabilir)

| Geçiş | Yazan | Kural |
|---|---|---|
| `new → processing` | worker (atomic claim) | tek sahip |
| `processing → scored/degraded` | worker | verdict yazıldıysa |
| `→ notified` | dispatcher | gönderim 2xx aldıysa |
| `→ failed` | worker/dispatcher | `next_attempt_at` mutlaka set |
| `failed → dead` | reaper veya dispatcher | `attempts >= MAX` |
| `filtered → new` | **sadece replay** | kural değişince yeniden işleme |

Son satır: triyaj kuralını değiştirdiğinde `filtered` kayıtlarını geri
açabilmelisin. Bu yüzden filtre "silme" değil, **durum**.

---

## 5. Gecikme bütçesi ve adaptif polling

Hedef: **KAP yayınından Telegram'a 60-90 saniye.**

| Adım | Bütçe |
|---|---|
| Poll aralığı (ortalama bekleme) | ~15 s |
| Fetch + parse | 1-3 s |
| DB insert + claim | <50 ms |
| Features + FS lookup | <50 ms |
| LLM çağrısı (p95) | 4-8 s |
| Dispatch (Telegram API) | 0.3-1 s |
| **Toplam p95** | **~30-45 s** |

Poll aralığı tek başına gecikmenin yarısı. Ama 3 saniyede bir sormak
rate-limit intiharı. Orta yol: **adaptif** (piyasa saatinde sık, dışında seyrek):

```python
def poll_interval(now_utc: datetime) -> int:
    now_trt = now_utc + timedelta(hours=3)
    if now_trt.weekday() >= 5:                       return 300   # hafta sonu
    if time(9, 30) <= now_trt.time() <= time(18, 15): return 15  # seans
    if time(18, 15) <  now_trt.time() <= time(23, 0): return 60  # akşam bildirimleri
    return 300
```

BIST yarım günleri ve tatiller: `config/bist_calendar.json` (elle 20 satır,
kütüphane bağımlılığına gerek yok). Tatilde adaptif poller "0 haber" görüp
alarm üretmesin — alarm mantığı **takvimi bilmeli**, yoksa her bayramda
yanlış alarm alırsın.

Ek: `ETag`/`If-Modified-Since`. 304 dönerse parse bile etme — CPU ve log
gürültüsü düşer.

---

## 6. Replay: sistemin kendini kanıtlama mekanizması

LLM'li sistemlerde en sık yapılan hata: prompt/eşik değiştir, "daha iyi
görünüyor" de, geçmişi bir daha test etme. Replay bunu mimariye gömer.

### 6.1 Neyi saklamak zorundayız (bunlar olmadan replay imkânsız)

Her `(disclosure, ticker)` için:
`raw_html` (veya içerik hash'i + arşiv), `features` (jsonb), `prompt_version`,
`model`, `params`, `raw_response`, `verdict`, `cost_usd`, `latency_ms`.

### 6.2 Replay modu

```
bistman replay --from 2025-01-01 --to 2025-06-30 --prompt v3 --dry-run
```

- **Yan etki yok:** DB yazımı `replay_*` şemasına, bildirim yok.
- LLM çağrıları `temperature=0` + `prompt_version` sabit.
- Çıktı: karşılaştırma raporu —

```
toplam 2.418 kayıt | verdict değişti: 312 (%12.9)
push edilen: 187 → 204   (+17)
push edilmeyen ama edilmeliydi (elle işaretli): 6
ortalama |Δimpact|: 1.4   maliyet: $2.10   p95 gecikme: 6.8 s
```

Bu rapordaki **"kaçırılan 6 kayıt"** asıl metrik. "Daha çok bildirim
gönderiyoruz" bir iyileşme değil, gürültü artışı olabilir.

### 6.3 Mimari sonucu (neden ports & adapters)

Replay'in çalışması için pipeline'ın yan etkileri **enjekte edilebilir**
olmalı: `LLMClient`, `Repository`, `Notifier` arayüz olarak geçer. Replay
bunların `ReplayLLM` / `ReplayRepo` / `NullNotifier` implementasyonlarını
verir. Yani klasör yapısı keyfi değil — replay'i mümkün kılmak için öyle.

### 6.4 Golden set (D9'un cevabı)

`eval/golden.jsonl`: 100-150 elle etiketlenmiş gerçek bildirim
(`impact`, `sentiment`, `push_edilmeli_mi`). Model/sağlayıcı seçimi bu sette
ölçülür: şema valid %, impact MAE, push precision/recall, maliyet.
"Model X daha iyi" cümlesi ancak bu sayıyla kurulur.

---

## 7. Gözlemlenebilirlik: sessiz ölümü yakalamak

### 7.1 Golden signals (hepsi tek `/metrics` veya DB view)

| Metrik | Alarm eşiği |
|---|---|
| `last_poll_success_at` | seans içinde 5 dk geçtiyse |
| `disclosures_per_hour` | seans içinde 2 saat 0 ise |
| `parse_fail_ratio` | > %20 (şablon değişti) |
| `queue_depth{status=new}` | > 500 (5 dk boyunca) |
| `llm_error_rate` | > %10 (15 dk) |
| `llm_cost_today` | > günlük bütçe (hard cap) |
| `notify_fail_ratio` | > %20 |
| `end_to_end_latency_p95` | > 180 s |
| `status=dead` sayısı | 1 saatte > 10 |

### 7.2 Öz-izleme ölür (dead man's switch)

Süreç çökerse kendi alarmını üretemez. Bu yüzden **dışarıdan** nabız:
`GET /health` her 5 dk healthchecks.io tarzı bir servise ping atar; ping
gelmezse servis sana mail/Telegram atar. Bu, "kod içinde alarm" ile
kapatılamayan tek delik.

### 7.3 Log disiplini

- Her satırda `disclosure_id` + `ticker` (correlation id) — olmadan 3 servis
  arasında iz süremezsin.
- `structlog` (JSON), `print` yok.
- LLM prompt/response **tam** loglanır ama PII yok (yok zaten).
- Hata logu: `exc_info=True` + hangi adımda (`step='llm'`).

---

## 8. Senin karar vermen gereken 5 soru

Cevaplar bu dosyayı ve kodu şekillendirir. Benim önerim parantezde.

1. **Hedef kitle tek kişi mi (sen) yoksa çok kullanıcı mı?** (tek kişi →
   auth/tenant yok, basit şema; çok kullanıcı → `user`, `subscription`,
   `delivery_pref` tabloları ve kanal bazlı eşik gerekir)
2. **Bildirim gecikmesi ne kadar kritik?** (60-90 sn yeter → adaptif poll 15 sn;
   "saniyeler" → KAP'ta resmî push yok, WebSocket dinleme kırılgan, hedefi
   gerçekçi tut)
3. **Geçmiş veri ne kadar geriye?** (replay için 12 ay ideal; 3 ay da yeterli,
   ama en az bir bilanço sezonu görmek lazım)
4. **Aynı hisse için günde kaç bildirim tolere edilir?** (cevap eşikleri
   belirler; öneri: hisse başına günlük max 3 anında push, gerisi özet)
5. **"Kaçırma" mı "yanlış alarm" mı daha pahalı?** (kaçırma → eşiği düşür +
   `confidence` düşükse "belirsiz" etiketiyle gönder; yanlış alarm → eşiği
   yükselt + günlük özet ağırlığını artır)
