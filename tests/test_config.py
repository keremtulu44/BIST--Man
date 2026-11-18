"""Universe config sözleşmesi: semboller borsapy'de doğrulanmış olanlar."""

from bistman.config import load_config

# borsapy 0.11.0 kaynak kodundan doğrulanmış (canlidoviz METAL_IDS / ENERGY_IDS / index.py)
VERIFIED_FX = {"gram-altin", "ons-altin", "gram-gumus", "BRENT", "USD"}
VERIFIED_INDEX = {"XU100", "XBANK"}


def test_seven_instruments() -> None:
    cfg = load_config()
    assert len(cfg.instruments) == 7


def test_all_symbols_verified_against_borsapy() -> None:
    cfg = load_config()
    for inst in cfg.instruments:
        pool = VERIFIED_FX if inst.kind == "fx" else VERIFIED_INDEX
        assert inst.symbol in pool, f"doğrulanmamış sembol: {inst.kind}:{inst.symbol}"


def test_brent_requires_tl_conversion() -> None:
    """Brent USD fiyatlı: TL çevrimi olmadan 'düştü' yorumu yanlış olur."""
    cfg = load_config()
    brent = next(i for i in cfg.instruments if i.symbol == "BRENT")
    assert brent.quote_ccy == "USD"
    assert brent.tl_conversion_required is True
    assert cfg.usdtry_required is True


def test_poll_intervals_are_sane() -> None:
    cfg = load_config()
    assert 5 <= cfg.poll_interval_market <= 60
    assert cfg.alert_sigma >= 1.5
    assert cfg.sigma_window_days >= 10
