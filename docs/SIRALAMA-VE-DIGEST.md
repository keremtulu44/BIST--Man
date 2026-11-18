# Önceki gönderim motoru: sıralama ve günlük özet

> Bu mekanizma, bir **gruptaki** gönderim adaylarını sıralar. [Grafik alarmındaki tek gelişme](ANA-MANTIK.md) ise puanla değil hisse, yayın, bilinirlik ve kanıtla seçilir. Örneğin önceki satış özeti push, sonraki fesih bilgisi digest ise `rank` kanala öncelik verip eski satışı tutabilir. Bu yüzden alarm sorgusu `rank` kullanmaz.

Modül: `src/bistman/digest.py` · demo: `python scripts\gunluk_ozet.py`

---

## Kurallar (`tests/test_digest.py`)

| # | Kural | Neden |
|---|---|---|
| 1 | **Tekilleştirme**: aynı `(index, hisse)` tek kayıt; gönderim kanalı, sonra o kanalın önem sırası belirleyici, tekrar sayılır | KAP aynı bildirimi iki kez verebiliyor; sessiz eleme yok |
| 2 | **Sıralama**: `push_score` ↓ (`push_uncertain` için `impact`), eşitlikte `impact` ↓, hisse, yayın zamanı, index | Deterministik: aynı girdi aynı sıra |
| 3 | **Bütçe**: tek gün için verilen grupta, hisse başına ≤ `max_push_per_ticker_per_day` push | Spam koruması; config'deki sayı artık gerçekten uygulanıyor |
| 4 | Bütçeyi aşan push **düşürülmez**, özete iner ve `bütçe` etiketi alır | Kaybetmek yok, sadece sırası değişir |
| 5 | **Özet**: sayısal digest olayları + bütçeden inenler, ardından ölçeksiz gündem; `max_items` sonrası tek satırda sayılır | Tek mesaj, okunur uzunluk |
| 6 | **Boş özet gönderilmez** (`None`), sayaçlar raporda | "Sessizlik = sinyal yok" değil, gönderilecek şey yok |

Gönderim sırası **kasıtlı**: önce push'lar (en önemli önce), özet **en sonda**.
Bu bir çağrının sırasıdır; gün sonu zamanlayıcısı henüz yok.

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

Sahte alıcının onayladığı örnek özet (düz metin):

```
GÜNLÜK ÖZET · 17.11.2026
4 olay · 3/3 ayrı push onaylandı

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
report.ok            # denenen gönderimler alıcı tarafından onaylandı mı?
report.summary()     # "push 3/3 · özet gönderildi · bütçe 1"
report.ranked        # sıralama (sayaçlarla)
```

`rank` **saf fonksiyondur**: DB, ağ, saat yok. `dispatch` yalnız `Notifier`'a
dokunur. Örnekler `FakeNotifier` ile çalışır; bir Telegram istemcisinin bulunması, üretimde çalışan bir gönderim süreci olduğu anlamına gelmez.

## Sözleşme değişikliği: `notifier` artık test yolu

`process_batch(notifier=...)` push'ları **geliş sırasıyla** gönderir — yani
sıralamayı ve bütçeyi atlar. Gönderim deneyinde sıralı plan için `notifier=None` verilip sonuçlar `dispatch`'e geçirilir. Alarm bağlamında `dispatch` hiç çağrılmaz. Doğrudan denenmiş bir sonuç aynı listede `dispatch`'e verilirse ikinci kez gönderilmez; çağrı ağ erişiminden önce reddedilir.

`rank`/`render_digest` yalnız plan bildirir. `dispatch` sonucu `pushed_ok`, `failed_push_keys`, `digest_sent` ve `digest_failed` ile onayı raporlar. `process_batch` doğrudan yolunda `summarize()["notified"]` yalnız olumlu alıcı yanıtlarını sayar; aday ve deneme ayrı sayaçlardır. [Ağsız hata deneyleri](GONDERIM-AKISI.md).

## Açık kalanlar (bilinçli)

| Konu | Durum |
|---|---|
| **Gün sınırı** | `day` dışarıdan verilir; `dispatch` başka TRT gününe (sabit UTC+3) ait kayıtları gönderimden önce reddeder. Seans bazlı gün istersek `event_study._t0` kullanılmalı |
| **Özet saati** | Bu modül saati seçmez veya kurulu bir zamanlayıcı başlatmaz. |
| **Kalıcılık** | Bütçe ve tekilleştirme yalnız **tek çağrıda** geçerli. Aynı süreçteki sonraki çağrıda da sıfırdan başlanır. Kalıcı `(gün, hisse)` sayacı yoktur. |
| **Tekrar gönderim** | `key` = `index:hisse`, özet için `digest:YYYY-MM-DD`; bu anahtarlar çağrılar arası teslim kaydı değildir. |
| **Sıralamanın kendisi** | Skor sıralaması, `impact`'in ne anlama geldiği kadar iyi (bkz. `ONEM-DENETIMI.md`). Sıralama mekaniği doğru; ölçek hâlâ kalibrasyon bekliyor |

## Tekrar üretmek

```bash
.venv/bin/python scripts/gunluk_ozet.py            # gerçek fixture + senaryo
.venv/bin/python scripts/gunluk_ozet.py --only-real
```
