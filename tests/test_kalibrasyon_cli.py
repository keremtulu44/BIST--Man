"""Demo koşusu gerçek raporu veya ölçülmüş ayarları değiştirmemeli."""

from pathlib import Path


def test_dry_run_preserves_real_outputs(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import kalibrasyon_gercek as cli

    report, measured = tmp_path / "rapor.md", tmp_path / "olcum.toml"
    for path in (report, measured):
        path.write_text("önceki gerçek koşu", encoding="utf-8")
    monkeypatch.setattr(cli, "REPORT", report)
    monkeypatch.setattr(cli, "MEASURED", measured)

    assert cli.run(dry_run=True, limit=None, bodies=True, max_bodies=0) == 0
    assert "DRY-RUN (sentetik fiyat)" in capsys.readouterr().out
    assert all(path.read_text(encoding="utf-8") == "önceki gerçek koşu" for path in (report, measured))
