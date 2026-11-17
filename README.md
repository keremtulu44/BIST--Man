# BIST--Man

KAP bildirimlerini izleyip BIST 50 şirketlerinin finansallarıyla birleştiren,
kural tabanlı skorlayan ve bildirim kanallarına dağıtan hat.
**Kişisel/eğitim amaçlı** (veri: borsapy, ticari kullanım lisansı yok).

## Durum

| Faz | İçerik | Durum |
|---|---|---|
| 0 | Saf ayrıştırma, triyaj, config | ✅ |
| 2a | KAP firehose istemcisi + ayrıştırıcılar (gerçek fixture, mock transport) | ✅ |
| 2b | Unvan→kod eşleme + oran/ceza motoru | ✅ |
| 1 | borsapy adaptörü + TTM elle doğrulama | ⏳ sırada (ağ gerekir) |
| 2c | Postgres şeması + dedup + cursor | |
| 3a | Event study motoru (CAR, z, t0 seçimi) + kalibrasyon ızgarası | ✅ |
| 3b | Kural skor (`config/scoring.toml`) + uçtan uca hat + Telegram istemcisi | ✅ |
| 4 | Gerçek veriyle kalibrasyon (KAP geçmişi ✅ ayrıştırılıyor + fiyat) | ⏳ sırada |

`pytest`: **248 test**, ağ erişimi gerektirmez.

## Dokümanlar

- **[docs/DURUM-RAPORU.md](docs/DURUM-RAPORU.md)** ← **bugüne kadarki özet: bulgular, ölçümler, riskler**
- **[docs/DOGRULAMA-ADIMLARI.md](docs/DOGRULAMA-ADIMLARI.md)** ← **senin yapacağın 2 doğrulama (PowerShell komutları)**
- **[docs/PLAN-V1.md](docs/PLAN-V1.md)** — v1 planı: evren, kalibrasyon yöntemi, fazlar
- **[docs/MIMARI.md](docs/MIMARI.md)** — yazılım mimarisi, veri modeli, hata kataloğu
- **[docs/TASARIM-DETAY.md](docs/TASARIM-DETAY.md)** — skor modeli, state machine, replay, karar kaydı
- **[docs/VERI-KATMANI-BORSAPY.md](docs/VERI-KATMANI-BORSAPY.md)** — veri katmanı + 4 sessiz tuzak
- **[docs/VERI-KAYNAKLARI.md](docs/VERI-KAYNAKLARI.md)** — kaynak referansı (resmî API'ler, lisanslar)

## Kullanım

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                      # ağ erişimi gerektirmez
```

## Yapı

```
src/bistman/
├── parsing/kap_table.py  # geçmiş bildirim tablosu (89 satır) + gövde TR-only metin
├── parsing/
│   ├── money.py     # TR tutar ayrıştırıcı (15.000.000 TL, 1,2 milyar dolar, ₺...)
│   ├── dates.py     # KAP'ın 3 farklı tarih formatı -> UTC
│   └── text.py      # tr_lower / name_norm (İ/I tuzağı)
├── triage.py        # ÖDA/FR/DKB -> pipeline | fundamentals_refresh | routine | mechanical
├── features.py      # oranlar (ciro/PD/net kâr) + çarpımsal confidence cezaları
├── scoring.py       # impact = konu_prior + oran_bucket; confidence çarpımsal; gerekçe üretir
├── pipeline.py      # uçtan uca hat: triyaj -> hisse -> oran -> skor -> bildirim
├── notify.py        # Telegram (parse_mode YOK, 4000'e böler, 429'a uyar) + FakeNotifier
├── analysis/ttm.py      # kümülatif/tekil tespiti + doğru TTM (borsapy 2x hatalı çıktı)
├── sources/isyatirim_labels.py  # kalem seçici (Roma rakamlı üst kalem) + tablo birleştirme
├── analysis/
│   ├── event_study.py   # t0 seçimi, CAR (endeks arındırılmış), z (oynaklık normalize)
│   └── calibration.py   # eşik × günlük bütçe ızgarası -> isabet/kaçan tablosu
├── ticker_map.py    # unvan -> ticker (exact + fuzzy + belirsizlik reddi)
├── config.py        # config/universe.toml yükleyici
└── sources/
    ├── base.py      # Protocol'ler + DisclosureStub/Detail/ParseResult
    └── kap.py       # firehose: list/light, detail, member_history + retry/backoff
```

## Demolar

```bash
.venv/bin/python scripts/hat_demusu.py            # GERÇEK KAP akışı -> skor -> bildirim (ağsız)
.venv/bin/python scripts/kalibrasyon_tablosu.py   # eşik/bütçe tablosu (sentetik)
```

Gerçek veriyle aynı kod çalışır (Faz 4): KAP geçmişi + fiyat serisi -> `ScoredEvent`
-> `sweep()`. Eşikler sezgiyle değil, "kaçan vs isabet" dengesinden seçilir.

## Tasarım kuralları (kısaca)

1. **Dedup DB'de**, bellekte değil (`ON CONFLICT DO NOTHING`).
2. **Kuyruk Postgres** (`FOR UPDATE SKIP LOCKED`) — Redis/Celery yok.
3. **LLM aritmetik yapmaz**; oranlar kodda hesaplanır.
4. **Boş sonuç = şüpheli**, "haber yok" değil → boşluk alarmı.
5. **Sessiz yanlış, gürültülü hatadan kötüdür**: tanınmayan format hata fırlatır.
