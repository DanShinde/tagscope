import csv
import io

from tagscope.storage import BAD, Sample


def test_sync_tags_is_idempotent(storage, config):
    first = storage.sync_tags(config.tags)
    config.tags[0].unit = "mm"
    second = storage.sync_tags(config.tags)
    assert first == second
    assert {t["name"]: t["unit"] for t in storage.list_tags()}["level"] == "mm"


def test_write_and_latest(storage):
    storage.write_samples([Sample("level", 1.0, 10.0), Sample("level", 2.0, 20.0), Sample("speed", 1.5, 3.0)])
    storage.write_samples([Sample("unknown", 1.0, 1.0)])  # ignored
    latest = storage.latest()
    assert latest["level"]["value"] == 20.0
    assert latest["speed"]["ts"] == 1.5
    assert storage.sample_count() == 3


def test_history_raw(storage):
    storage.write_samples([Sample("level", float(t), float(t)) for t in range(100)])
    h = storage.history("level", 10, 19, max_points=600)
    assert h["t"] == [float(t) for t in range(10, 20)]
    assert h["avg"] == h["min"] == h["max"]


def test_history_downsampled(storage):
    storage.write_samples([Sample("level", float(t), float(t % 10)) for t in range(1000)])
    h = storage.history("level", 0, 1000, max_points=100)
    assert len(h["t"]) == 100
    assert all(lo <= avg <= hi for lo, avg, hi in zip(h["min"], h["avg"], h["max"], strict=True))
    assert h["min"][0] == 0 and h["max"][0] == 9
    assert h["t"] == sorted(h["t"])


def test_history_unknown_tag_or_empty_range(storage):
    assert storage.history("nope", 0, 10)["t"] == []
    assert storage.history("level", 10, 0)["t"] == []


def test_export_csv(storage):
    storage.write_samples([Sample("level", 0.5, 1.0), Sample("speed", 1.0, 2.0, BAD)])
    text = "".join(storage.export_csv(None, 0, 10))
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["timestamp_utc", "epoch_s", "tag", "value", "quality"]
    assert rows[1] == ["1970-01-01T00:00:00.500Z", "0.500", "level", "1.0", "good"]
    assert rows[2][2:] == ["speed", "2.0", "bad"]
    only_level = "".join(storage.export_csv(["level"], 0, 10))
    assert "speed" not in only_level


def test_purge_and_events(storage):
    storage.write_samples([Sample("level", 1.0, 1.0), Sample("level", 100.0, 2.0)])
    storage.add_event("alarm", "old", "level", ts=1.0)
    storage.add_event("alarm", "new", "level", ts=100.0)
    assert storage.purge(50.0) == 1
    assert storage.sample_count() == 1
    assert [e["message"] for e in storage.events()] == ["new"]
