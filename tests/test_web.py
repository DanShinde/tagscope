import pytest
from fastapi.testclient import TestClient

from tagscope.hub import Hub
from tagscope.storage import Sample
from tagscope.web import create_app


@pytest.fixture
def client(config, storage):
    hub = Hub(config)
    hub.update("level", 95, ts=100)
    storage.write_samples([Sample("level", float(t), float(t)) for t in range(0, 600, 10)])
    storage.add_event("alarm", "level HIGH", "level", ts=100)
    return TestClient(create_app(hub, storage)), hub


def test_index_and_static(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "TagScope" in r.text
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/vendor/uPlot.iife.min.js").status_code == 200


def test_overview(client):
    c, _ = client
    data = c.get("/api/overview").json()
    assert [t["name"] for t in data["tags"]] == ["level", "speed", "running"]
    assert data["values"]["level"]["state"] == "high"
    assert data["servers"][0]["name"] == "plc"


def test_history(client):
    c, _ = client
    data = c.get("/api/history", params={"tag": "level", "minutes": 5, "end": 600}).json()
    assert data["t"][0] == 300.0 and data["t"][-1] == 590.0
    assert c.get("/api/history", params={"tag": "nope"}).status_code == 404


def test_events_and_export(client):
    c, _ = client
    assert c.get("/api/events").json()[0]["message"] == "level HIGH"
    r = c.get("/api/export.csv", params={"tags": "level", "minutes": 1, "end": 30})
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[1].split(",")[2] == "level"


def test_browse_unknown_server(client):
    c, _ = client
    assert c.get("/api/browse", params={"server": "nope"}).status_code == 404


def test_websocket_snapshot_and_updates(client):
    c, hub = client
    with c.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["values"]["level"]["value"] == 95
        hub.update("level", 50, ts=200)
        batch = ws.receive_json()
        assert batch["type"] == "batch"
        kinds = [m["type"] for m in batch["messages"]]
        assert "value" in kinds
