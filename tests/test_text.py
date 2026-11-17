"""Türkçe normalizasyon testleri — İ/I tuzağı ve ASCII katlama."""

from bistman.parsing.text import name_norm, tr_lower


def test_tr_lower_turkish_rules() -> None:
    assert tr_lower("İSTANBUL") == "istanbul"   # İ -> i (Python'da 'i̇' olurdu)
    assert tr_lower("IŞIK") == "ışık"           # I -> ı (Python'da 'işik' olurdu)
    assert tr_lower("ŞİŞLİ") == "şişli"
    assert tr_lower("") == ""


def test_name_norm_strips_legal_suffix() -> None:
    assert name_norm("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.") == "matriks finansal teknolojiler"
    assert name_norm("AKBANK T.A.Ş.") == "akbank"
    assert name_norm("TÜRK HAVA YOLLARI A.O.") == "turk hava yollari"


def test_name_norm_ascii_fold_makes_variants_equal() -> None:
    """Aynı şirket, iki farklı yazım -> aynı anahtar (eşleşme kaçmasın)."""
    assert name_norm("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.") == name_norm(
        "MATRIKS FINANSAL TEKNOLOJI"
    ) or name_norm("MATRİKS FİNANSAL TEKNOLOJİLER A.Ş.").startswith(
        name_norm("MATRIKS FINANSAL TEKNOLOJI")
    )
    assert name_norm("TÜRKİYE ŞİŞE VE CAM FABRİKALARI A.Ş.") == name_norm(
        "TURKIYE SISE VE CAM FABRIKALARI A.S."
    )


def test_name_norm_is_idempotent() -> None:
    once = name_norm("TÜRKİYE ŞİŞE VE CAM FABRİKALARI A.Ş.")
    assert name_norm(once) == once
