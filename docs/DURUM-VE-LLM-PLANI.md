# Durum Değerlendirmesi ve LLM Planı

İki soru: **(1) sistem şu an nasıl?** (2) **normal bir AI API'si bağlanınca ne
değişir, nereye bağlanmalı?**

Kısa cevap: sistem **%80 veri + karar motoru, %20 ürün**. LLM bu dengeyi
bozmadan, tam da ölçtüğümüz **üç kör noktaya** oturur — skor tablosunu
değiştirmeden.

---

## 1 · Şu an nerede

### Sağlam olan (kanıtlı)

| Alan | Kanıt |
|---|---|
| Veri katmanı | TTM hatası bulundu ve düzeltildi (naif 1.99× şişme); tablo ayrımı, banka/sanayi şablonu, itemCode eşlemesi gerçek sayılarla doğrulandı |
| Ağsız çalışma | 350 test, ağ/DB/anahtar yok; kurulum yok (stdlib istemciler) |
| Metin | Türkçe normalizasyon (İ/I/ı, hukuki ek, ASCII katlama) ve TR/EN sayı ayrıştırma testli |
| Triyaj | Gerçek 89 bildirimde gürültünün %74'ü skor görmeden düşüyor |
| Hat | triyaj → tutar → oran → skor → sıralama → özet, uçtan uca ve deterministik |
| Sıralama/özet | Önem sırası, günlük bütçe, `bütçe` etiketli düşürme, düz metin özet |
| Kalibrasyon iskeleti | `--dry-run` ile hat; gövde indirme + fiyat + CAR + eşik ızgarası hazır |

### Zayıf / eksik olan (dürüst liste)

| Eksik | Etkisi | Durum |
|---|---|---|
| **Gerçek veriyle kalibrasyon** | Eşikler hâlâ sezgi (6.5 / 3.5) | koşu sende: `kalibrasyon_gercek.py` |
| **DB + daemon yok** | Sistem 7/24 çalışmıyor; bütçe bellekte, dedup yok | Faz 2c |
| **Telegram uçtan uca yok** | `TelegramNotifier` var, çalıştıran süreç yok | Faz 2c sonrası |
| **`impact` üç şeyi taşıyor** | Aynı sayı farklı anlam; sıralama %100'de kırılıyor | `ONEM-DENETIMI.md` |
| **Yön yok** | Kötü haber iyi haberle aynı ölçekte, sessizleşiyor | bu dosya §2 |
| **Tutarsız maddi haber kör** | Konkordato/yangın/işlem yasağı → impact 0 | bu dosya §2 |
| **Konu önseli %83 boş** | 23 pipeline satırının 19'u `varsayılan` | kalibrasyon + §2 |
| **Döviz kör** | USD/EUR tutarlar `fx_fallback` | küçük FX tablosu |
| **Bilançonun 5/6'sı atıl** | Net kâr, özkaynak, PD kullanılmıyor | tasarım kararı |

**Yorum:** "Sezgiyle değil tabloyla" ilkesine en yakın parça veri katmanı.
Karar katmanı ise **henüz tabloyla karar vermiyor**, çünkü tablo boş (eşikler
sezgi). Bu yüzden sıradaki en değerli tek iş kalibrasyon koşusu.

---

## 2 · LLM nereye bağlanır

LLM **skor motorunun yerine değil, eksik girdilerin yerine** geçmeli. Dört iş,
kâr/risk sırasıyla:

| # | İş | Bugünkü durum | LLM katkısı | Risk |
|---|---|---|---|---|
| 1 | **Konu + yön + önem sınıfı** (başlık+gövde) | 23 satırın 19'unda önsel 0; yön yok | En yüksek: F1, F2, F5, F9'u birlikte kapatır | Orta — şema ile sınırlanır |
| 2 | **Ana tutar ifadesini seçme** | "en büyük tutar" kuralı; pay/eski-yeni kalıplarında yanılıyor | Hangi ifadenin ana tutar olduğunu işaret eder; **sayıyı kod hesaplar** | Düşük (sayı LLM'den gelmiyor) |
| 3 | **Özet metni** | Şablon | Günlük özete tek cümlelik "ne oldu" | Çok düşük |
| 4 | **Benzer bildirimleri kümeleme** | yok | Aynı haberin 5 kopyası tek satır | Düşük |

LLM'in **kesinlikle yapmadığı** işler:

- oran/TTM/skor hesaplamak (aritmetik = sessiz hata kaynağı),
- push/özet/sessiz kararı vermek (politika tablosu),
- eşik/önsel sayısı önermek (kalibrasyonun işi; "sezgiyle değil tabloyla").

### Mimari: önerir → doğrular → kural karar verir

```
KAP metni ──► LlmSubjectReader ──► SubjectRead(subject_key, severity, direction,
            (veya RuleSubjectReader)              confidence, amount_hint)
                                              │
                                    şema doğrulaması (parse_subject_read)
                                              │ bozuksa → None → kurala düş
                                              ▼
                          apply_policy(): force_notify (kritik), dir_label
                                              │
                       mevcut skor hattı (değişmiyor) ──► karar + mesaj etiketi
```

Kod: `src/bistman/llm.py` (bu turda yazıldı, **ağ yok**, 18 test).

- `complete(prompt) -> str` **enjekte edilir**: OpenAI, Anthropic, Gemini, yerel
  model — hepsi aynı arayüz. Modül hiçbir sağlayıcıyı bilmez, anahtar tutmaz.
- `parse_subject_read` **katı şema**: şemada olmayan alan gelirse (ör. `score`)
  tüm yanıt reddedilir. "Model skor uydurdu" hatası yapısal olarak imkânsız.
- Hata/timeout/bozuk JSON → `None` → `RuleSubjectReader` (sözlük tabanlı bugünkü
  davranış) devreye girer. **LLM çökerse hat çalışmaya devam eder.**
- `guven < 0.5` → öneri kullanılmaz (`reliable`).

Bugünkü kural okuyucu ile LLM'in farkı (temsili yanıtlarla):

```
bildirim                              kural            LLM                 force_notify
Konkordato Talebi                     kritik/olumsuz   kritik/olumsuz/0.92  ✅
Vergi Cezası                          rutin/olumsuz    onemli/olumsuz/0.88  —
SPK İşlem Yasağı                      kritik/olumsuz   kritik/olumsuz/0.90  ✅
```

`force_notify` = tutar olmasa bile bildir (F2'nin çözümü). Bu alan **skora
dokunmaz**; yalnız "sessiz kalma" kararını ezer.

---

## 3 · Bağlarken uyulacak 6 kural

1. **Sıcaklık 0, JSON modu, kısa istem.** Aynı girdi aynı çıktı; replay mümkün.
2. **Yalnız adaylar için çağır.** Triyajdan geçen satırlar (günde ~10-30), tüm
   akış değil. Maliyet ~0,01-0,05 USD/gün.
3. **Her çağrıyı logla**: model, istem hash'i, yanıt, süre, token, karar etkisi.
   Kalibrasyon "LLM ne kattı" sorusunu ancak bu logla cevaplar.
4. **Sessiz düşüş yok.** `None` dönerse `status`/`notes`'a yazılır
   (`llm_unavailable`), öylece görünür.
5. **Anahtar opsiyonel.** Anahtar yoksa `RuleSubjectReader`; hat aynı, ürün aynı
   — sadece kör noktalar geri gelir.
6. **Kapatma düğmesi.** `llm.enabled=false` → tek satır config, davranış bugünkü
   hâline döner. Ölçmeden açık bırakılmaz.

## 4 · "LLM işe yaradı mı" nasıl ölçülür

Kalibrasyon koşusu bunu zaten verecek:

1. **Etiket**: gerçek 200-300 bildirim, CAR ve elle okuma ile
   `{konu, yön, kritik mi, maddi mi}`.
2. **Üç koşu**: (a) yalnız kurallar, (b) kurallar + LLM sınıfı,
   (c) kurallar + LLM + yön etiketi.
3. **Metrik**: `kaçan maddi olay` (birincil), `isabet`, `günlük push sayısı`,
   `yanlış kritik`, token maliyeti.
4. **Kabul ölçütü**: kaçan maddi olayda anlamlı düşüş, isabette düşüş yok.
   Aksi hâlde LLM kapalı kalır.

---

## 5 · Sıradaki iş sırası (öneri)

| Sıra | İş | Neden şimdi |
|---|---|---|
| 1 | Kalibrasyon koşusu (sende) | Eşikler sezgiden ölçüme geçmeden LLM'in katkısı ölçülemez |
| 2 | Faz 2c: Postgres + dedup + cursor + daemon | Sistemin 7/24 çalışması; bütçe/dedup kalıcı olur |
| 3 | `force_notify` + yön etiketini hatta bağla (LLM'siz, kural okuyucu ile) | F2/F9 bugün kapanır; LLM sonra sadece kaliteyi artırır |
| 4 | FX tablosu (günlük TCMB kuru) | Döviz körlüğü ucuz kapanır |
| 5 | LLM bağlantısı (tek sağlayıcı, `complete()` enjeksiyonu) | Ölçüm altyapısı hazır olduktan sonra |

Not: **3. adım LLM'siz de yapılabilir** ve bugün 12 vakanın 4'ünü görünür kılar.
LLM bu adımın yerine geçmez, üstüne kalite koyar.

## Tekrar üretmek

```bash
.venv/bin/pytest tests/test_llm.py -q     # 18 test, ağ yok
```
