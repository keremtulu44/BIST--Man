# Sıralama ve Günlük Özet

Denetim iki şeyi göstermişti: canlı hatta **sıralama yok** (gönderim = KAP'ın
yayın sırası), `digest` bir **etiket** ve tüketicisi yok, `max_push_per_ticker_per_day`
ayarda duruyor ama hiçbir kod uygulamıyor. Bu turda üçü de kapandı.

Modül: `src/bistman/digest.py` · demo: `python scripts\gunluk_ozet.py`

---

## Kurallar (hepsi testli: `tests/test_digest.py`, 15 test)

| # | Kural | Neden |
|---|---|---|
| 1 | **Tekilleştirme**: aynı `(index, hisse)` iki kez gelirse en yüksek skorlu kalır, tekrar sayılır | KAP aynı bildirimi iki kez verebiliyor; sessiz eleme yok |
| 2 | **Sıralama**: `push_score` ↓, eşitlikte `impact` ↓, hisse, yayın zamanı, index | Deterministik: aynı girdi aynı sıra |
| 3 | **Bütçe**: hisse başına günde ≤ `max_push_per_ticker_per_day` push | Spam koruması; config'deki sayı artık gerçekten uygulanıyor |
| 4 | Bütçeyi aşan push **düşürülmez**, özete iner ve `bütçe` etiketi alır | Kaybetmek yok, sadece sırası değişir |
| 5 | **Özet**: digest olayları + bütçeden inenler, skora göre; `max_items` sonrası tek satırda sayılır | Tek mesaj, okunur uzunluk |
| 6 | **Boş özet gönderilmez** (`None`), sayaçlar raporda | "Sessizlik = sinyal yok" değil, gönderilecek şey yok |

Gönderim sırası **kasıtlı**: önce push'lar (en önemli önce), özet **en sonda**.
Gün içinde tek tek bildirim, gün sonunda tek özet.

## Gerçek çıktı (senaryo: sentetik tutarlar, gerçek mekanizma)

```
GELİŞ sırası (KAP yayını)     │ ÖNEM sırası (skor, bütçe uygulanmış)
MTRKS  10.00 Yeni Sözleşme    │ push  MTRKS  10.00 Yeni Sözleşme
THYAO   4.50 Yeni Sözleşme    │ push  MTRKS   9.00 Birleşme
MTRKS   8.50 Yatırım Kararı   │ push  MTRKS   8.50 Yatırım Kararı
THYAO   1.00 Temettü          │ özet* MTRKS   7.50 Temettü        ← bütçe
MTRKS   7.50 Temettü          │ özet  MTRKS   6.00 Satın Alma
MTRKS   6.00 Satın Alma       │ özet  MTRKS   5.50 Kapasite
MTRKS   5.50 Kapasite         │ özet  THYAO   4.50 Yeni Sözleşme
MTRKS   9.00 Birleşme         │
```

Telegram'a giden özet metni (düz metin, MarkdownV2 yok):

```
GÜNLÜK ÖZET · 17.11.2026
4 olay · 3 push ayrıca gönderildi

1. MTRKS · Temettü Dağıtımı
   skor 7.50 · impact 7.5 · oran %15.6 · bütçe
2. MTRKS · Satın Alma
   skor 6.00 · impact 6.0 · oran %9.4
3. MTRKS · Kapasite Artışı
   skor 5.50 · impact 5.5 · oran %3.8
4. THYAO · Yeni Sözleşme İmzalanması
   skor 4.50 · impact 4.5 · oran %1.1
```

Önemli ayrıntı: bütçe nedeniyle düşen **7.50**'lik temettü, özette 6.00'lık satın
almanın **üstünde** — sıra gerçekten skorla, düşürme sırayı bozmuyor.

## API

```python
from bistman.digest import rank, render_digest, dispatch

ranked = rank(results, budget_per_ticker=cfg.max_push_per_ticker_per_day)
text   = render_digest(ranked, day=date(2026, 11, 17))   # None olabilir

report = await dispatch(results, notifier, day=date(2026, 11, 17), cfg=cfg)
report.ok            # push'ların hepsi gitti ve özet (varsa) gitti mi
report.summary()     # "push 3/3 · özet gönderildi · bütçe 1"
report.ranked        # sıralama (sayaçlarla)
```

`rank` **saf fonksiyondur**: DB, ağ, saat yok. `dispatch` yalnız `Notifier`'a
dokunur. Böylece aynı kod replay'de (`FakeNotifier`) ve üretimde (`TelegramNotifier`)
çalışır.

## Sözleşme değişikliği: `notifier` artık test yolu

`process_batch(notifier=...)` push'ları **geliş sırasıyla** gönderir — yani
sıralamayı ve bütçeyi atlar. Üretimde `notifier=None` verilip sonuçlar
`dispatch`'e geçmeli. İkisi de kod içinde yorumla işaretlendi.

## Açık kalanlar (bilinçli)

| Konu | Durum |
|---|---|
| **Gün sınırı** | `day` dışarıdan verilir; TRT takvim günü varsayılıyor (sabit UTC+3). Seans bazlı gün istersek `event_study._t0` kullanılmalı |
| **Özet saati** | Yeni gün için tek sefer: 18:10 TRT (kapanış sonrası) öneriliyor; zamanlayıcı Faz 2c'de |
| **Kalıcılık** | Bütçe sayacı bugün **bellekte**; süreç yeniden başlarsa sıfırlanır. DB geldiğinde (Faz 2c) `(gün, hisse)` sayacı kalıcı olacak |
| **Tekrar gönderim** | `key` = `index:hisse`, özet için `digest:YYYY-MM-DD`; gerçek dedup DB'de (`ON CONFLICT DO NOTHING`) |
| **Sıralamanın kendisi** | Skor sıralaması, `impact`'in ne anlama geldiği kadar iyi (bkz. `ONEM-DENETIMI.md`). Sıralama mekaniği doğru; ölçek hâlâ kalibrasyon bekliyor |

## Tekrar üretmek

```bash
.venv/bin/python scripts/gunluk_ozet.py            # gerçek fixture + senaryo
.venv/bin/python scripts/gunluk_ozet.py --only-real
```
