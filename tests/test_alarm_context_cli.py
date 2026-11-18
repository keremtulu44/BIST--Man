"""Gerçek kayıtla dış alarmın gözünden: hazırlık ayrı, sorgu salt okunur."""

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "alarm_baglami.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_mtrks_real_archive_before_at_after_and_other_stock(tmp_path):
    db = tmp_path / "kap.sqlite"
    prepared = run("hazirla-demo", db)
    assert prepared.returncode == 0, prepared.stderr
    assert json.loads(prepared.stdout)["sonuclar"]["hazir"] == 1
    before = db.read_bytes()
    assert run("hazirla-demo", db).returncode != 0  # gerçek arşiv geriye tarihlenemez

    def query(ticker, value):
        process = run("sorgula", db, ticker, value)
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    assert query("MTRKS", "2026-11-17T14:50:00+03:00")["durum"] == "yok"
    hit = query("BIST:MTRKS", "2026-11-17T14:53:07+03:00")["baglam"]
    assert hit == query("mtrks", "2026-11-17T15:00:00+03:00")["baglam"]
    assert hit["kaynak"] == "https://www.kap.org.tr/tr/Bildirim/1699030"
    assert hit["yayin_zamani"] == "2026-11-17T11:53:07+00:00"
    assert "2024/35" in hit["olay"] and "yaptırım açıklanmadığını belirtti" in hit["olay"]
    assert "yeni bir ceza bildirmiyor" in hit["onemi"]
    for other in ("FONET", "ORNEK", "BASKA"):
        assert query(other, "2026-11-17T15:00:00+03:00")["mesaj"] == "İlgili güncel gelişme bulunamadı."
    assert db.read_bytes() == before


def test_missing_database_is_an_error_not_an_empty_answer(tmp_path):
    db = tmp_path / "typo.sqlite"
    result = run("sorgula", db, "MTRKS", "2026-11-17T15:00:00+03:00")
    assert result.returncode != 0 and "Hata:" in result.stderr and result.stdout == ""
    assert not db.exists()
