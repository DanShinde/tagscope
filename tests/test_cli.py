import pytest

from tagscope import __version__
from tagscope.cli import build_parser, main


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_init_writes_config(tmp_path):
    path = tmp_path / "tagscope.yaml"
    assert main(["init", str(path)]) == 0
    assert "servers:" in path.read_text(encoding="utf-8")
    assert main(["init", str(path)]) == 1  # refuses to overwrite
    assert main(["init", str(path), "--force"]) == 0


def test_run_reports_config_errors(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("servers:\n  - name: x\n", encoding="utf-8")
    assert main(["run", str(path)]) == 2
    assert "endpoint" in capsys.readouterr().err


def test_export_to_file(tmp_path):
    db = tmp_path / "t.db"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"storage: sqlite:///{db}\nservers: []\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    assert main(["export", str(cfg), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("timestamp_utc")


def test_parser_has_all_commands():
    parser = build_parser()
    for cmd in ["demo", "run", "init", "browse", "export"]:
        assert parser.parse_args([cmd, "x"] if cmd in {"run", "browse", "export"} else [cmd]).command == cmd
