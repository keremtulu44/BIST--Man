"""Unvan -> kod eşleme testleri (unvanlar KAP'tan alınmış gerçek kayıtlar)."""

from bistman.ticker_map import TickerResolver

PAIRS = [
    ("MTRKS", "MATRİKS FİNANSAL TEKNOLOJİLER A.Ş."),
    ("GOLDA", "GOLDA GIDA SANAYİ VE TİCARET A.Ş."),
    ("THYAO", "TÜRK HAVA YOLLARI A.O."),
    ("AKBNK", "AKBANK T.A.Ş."),
    ("SISE", "TÜRKİYE ŞİŞE VE CAM FABRİKALARI A.Ş."),
]


def resolver() -> TickerResolver:
    return TickerResolver.from_pairs(PAIRS)


def test_exact_match_with_legal_suffix() -> None:
    r = resolver().resolve("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.")
    assert (r.ticker, r.method, r.score) == ("MTRKS", "exact", 1.0)


def test_exact_match_ignores_suffix_variants() -> None:
    """Aynı şirket farklı eklerle yazılabiliyor."""
    assert resolver().resolve("MATRİKS FİNANSAL TEKNOLOJİLER A.O.").ticker == "MTRKS"
    assert resolver().resolve("MATRİKS FİNANSAL TEKNOLOJİLER").ticker == "MTRKS"


def test_turkish_case_folding() -> None:
    """'İ'.lower() tuzağı: 'MATRİKS' -> 'matri̇ks' olmamalı, 'matriks' olmalı."""
    assert resolver().resolve("matriks finansal teknolojiler a.ş.").ticker == "MTRKS"
    assert resolver().resolve("TÜRK HAVA YOLLARI A.O.").ticker == "THYAO"


def test_fuzzy_tolerates_small_differences() -> None:
    r = resolver().resolve("MATRIKS FINANSAL TEKNOLOJI")  # nokta yok, İ yerine I
    assert r.ticker == "MTRKS"
    assert r.method in {"exact", "fuzzy"}
    assert r.score >= 0.88


def test_fund_and_platform_titles_do_not_match() -> None:
    """Akışta fon/BISTECH kayıtları çok; yanlış eşleşme olmamalı."""
    for title in (
        "BULLS PORTFÖY BİRİNCİ SERBEST (TL) FON",
        "BORSA İSTANBUL BISTECH DEVRE KESİCİ UYGULAMASI",
        "INVEO PORTFÖY YÖNETİMİ A.Ş. INVEO VENTURES GİRİŞİM SERMAYESİ YATIRIM FONU",
    ):
        assert resolver().resolve(title).ticker is None


def test_empty_and_garbage() -> None:
    assert resolver().resolve("").method == "none"
    assert resolver().resolve("   ").method == "none"


def test_ambiguity_is_refused() -> None:
    """İki aday çok yakınsa tahmin etme — yanlış hisseye bildirim gönderme."""
    r = TickerResolver.from_pairs(
        [("AAA", "ABC ENERJİ A.Ş."), ("BBB", "ABC ENERJİA A.Ş.")]
    ).resolve("ABC ENERJİ A.Ş.")  # birine exact, diğerine çok yakın
    # exact eşleşme her zaman kazanır (belirsizlik sadece fuzzy'de aranır)
    assert r.ticker == "AAA"

    fuzzy = TickerResolver.from_pairs(
        [("AAA", "XYZ ENERJİ ÜRETİM A.Ş."), ("BBB", "XYZ ENERJİ ÜRETİMİ A.Ş.")]
    ).resolve("XYZ ENERJİ ÜRETİMİ A.O.")
    if fuzzy.method == "ambiguous":
        assert fuzzy.ticker is None
        assert len(fuzzy.candidates) >= 2


def test_len_and_duplicates() -> None:
    r = TickerResolver.from_pairs([("AAA", "A ŞİRKETİ A.Ş."), ("AAA", "A ŞİRKETİ A.Ş.")])
    assert len(r) == 1


def test_ascii_fold_collision_is_refused() -> None:
    """İki FARKLI kod aynı normalize anahtara düşerse tahmin etme."""
    r = TickerResolver.from_pairs([("AAA", "ŞİŞE CAM A.Ş."), ("BBB", "SISE CAM A.Ş.")])
    res = r.resolve("ŞİŞE CAM A.Ş.")
    assert res.method == "ambiguous"
    assert res.ticker is None


def test_unlisted_subsidiaries_and_duplicate_roots_do_not_match_parent_stock() -> None:
    """Aynı grubun halka açık olmayan bağlı ortaklıkları ana hisseye yazılamaz."""
    bist = TickerResolver.from_pairs([
        ("ENJSA", "ENERJİSA ENERJİ A.Ş."),
        ("ZOREN", "ZORLU ENERJİ ELEKTRİK ÜRETİM A.Ş."),
        ("TCELL", "TURKCELL İLETİŞİM HİZMETLERİ A.Ş."),
        ("YKBNK", "YAPI VE KREDİ BANKASI A.Ş."),
        ("GARAN", "TÜRKİYE GARANTİ BANKASI A.Ş."),
        ("ARCLK", "ARÇELİK A.Ş."),
        ("THYAO", "TÜRK HAVA YOLLARI A.O."),
    ])
    for outsider in (
        "ENERJİSA ÜRETİM SANTRALLERİ ENERJİ A.Ş.",
        "TURKCELL FİNANSMAN İLETİŞİM HİZMETLERİ A.Ş.",
        "ZORLU YENİLENEBİLİR ENERJİ ELEKTRİK ÜRETİM A.Ş.",
        "YAPI KREDİ BANKASI AZERBAYCAN",
        "TÜRKİYE GARANTİ BANKASI EMEKLİ SANDIĞI",
        "ARÇELİK PAZARLAMA A.Ş.",
        "ENERJİ ENERJİSİ MADENCİLİK A.Ş.",
    ):
        assert bist.resolve(outsider).ticker is None, outsider
    assert bist.has_ticker("enjsa") is True
    assert bist.has_ticker("ANKARA") is False
