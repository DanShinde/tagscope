import csv

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


def _export_db(tmp_path, samples):
    """A config + database with one tag and samples at the given epoch timestamps."""
    db = tmp_path / "t.db"
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"storage: sqlite:///{db}\nservers: []\n", encoding="utf-8")
    from tagscope.config import TagConfig
    from tagscope.storage import Sample, Storage

    storage = Storage(f"sqlite:///{db}")
    storage.sync_tags([TagConfig(name="level", node="ns=2;s=level")])
    storage.write_samples([Sample("level", ts, float(ts)) for ts in samples])
    storage.close()
    return cfg


def _export_rows(cfg, tmp_path, *extra):
    out = tmp_path / "out.csv"
    assert main(["export", str(cfg), "-o", str(out), *extra]) == 0
    with out.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_export_since_until_exports_only_the_window(tmp_path):
    cfg = _export_db(tmp_path, [1000.0, 2000.0, 3000.0, 4000.0])
    # 1800..3600 epoch = 1970-01-01T00:30:00Z .. 01:00:00Z
    rows = _export_rows(cfg, tmp_path, "--since", "1970-01-01T00:30:00+00:00", "--until", "1970-01-01T01:00:00+00:00")
    assert [r["epoch_s"] for r in rows] == ["2000.000", "3000.000"]


def test_export_since_alone_runs_until_now_and_ignores_minutes(tmp_path):
    cfg = _export_db(tmp_path, [1000.0, 2000.0])
    # --minutes 0 would export nothing on its own; --since overrides it.
    rows = _export_rows(cfg, tmp_path, "--since", "1970-01-01T00:00:00+00:00", "--minutes", "0")
    assert [r["epoch_s"] for r in rows] == ["1000.000", "2000.000"]
    # Without --since, --minutes 0 leaves an empty window around now.
    out = tmp_path / "empty.csv"
    assert main(["export", str(cfg), "-o", str(out), "--minutes", "0"]) == 0
    assert list(csv.DictReader(out.open(encoding="utf-8"))) == []


def test_export_accepts_date_only_and_naive_local_time(tmp_path):
    import datetime as dt

    from tagscope.cli import _export_window, _parse_iso8601

    # Use 2026 timestamps: 1970-era local times break on Windows (astimezone()
    # raises OSError for pre-1970) and are timezone-fragile west of UTC.
    row = dt.datetime(2026, 11, 21, 18, 0, tzinfo=dt.timezone.utc).timestamp()
    cfg = _export_db(tmp_path, [row])
    rows = _export_rows(cfg, tmp_path, "--since", "2026-11-21", "--until", "2026-11-23T00:00:00+00:00")
    assert [r["epoch_s"] for r in rows] == [f"{row:.3f}"]
    naive = _parse_iso8601("2026-11-21T06:00")
    assert naive == dt.datetime.fromisoformat("2026-11-21T06:00").astimezone().timestamp()
    end = _export_window(build_parser().parse_args(["export", "x", "--minutes", "60"]))
    assert end[1] - end[0] == 60 * 60


def test_export_pre_epoch_date_is_a_clean_error_not_a_traceback():
    # On Windows, astimezone() on a pre-1970 local time raises OSError; the
    # CLI must turn that into the friendly "cannot parse" ValueError (exit 2).
    from tagscope.cli import _parse_iso8601

    try:
        _parse_iso8601("1970-01-01")
    except ValueError as e:
        assert "cannot parse" in str(e)


def test_export_rejects_since_after_until(tmp_path, capsys):
    cfg = _export_db(tmp_path, [1000.0])
    rc = main(["export", str(cfg), "--since", "1970-01-02T00:00:00+00:00", "--until", "1970-01-01T00:00:00+00:00"])
    assert rc == 2
    assert "after --until" in capsys.readouterr().err


def test_export_rejects_unparseable_timestamps(tmp_path, capsys):
    cfg = _export_db(tmp_path, [1000.0])
    rc = main(["export", str(cfg), "--since", "yesterday-ish"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot parse" in err and "yesterday-ish" in err


def test_parser_has_all_commands():
    parser = build_parser()
    for cmd in ["demo", "run", "init", "browse", "export"]:
        assert parser.parse_args([cmd, "x"] if cmd in {"run", "browse", "export"} else [cmd]).command == cmd
