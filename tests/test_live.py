"""End to end: simulated OPC UA server -> collector -> hub -> database -> HTTP API."""

import asyncio
import contextlib

import pytest
from fastapi.testclient import TestClient

from tagscope.collector import browse, flush, watch_server
from tagscope.hub import Hub
from tagscope.simulator import Plant, demo_config, run_simulator
from tagscope.storage import Storage
from tagscope.web import create_app

from .conftest import free_port


@pytest.fixture
async def plant_endpoint():
    endpoint = f"opc.tcp://127.0.0.1:{free_port()}/tagscope/test"
    ready = asyncio.Event()
    task = asyncio.create_task(run_simulator(endpoint, rate_hz=10, plant=Plant(seed=1), ready=ready))
    await asyncio.wait_for(ready.wait(), 15)
    yield endpoint
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_collect_from_simulated_plant(plant_endpoint):
    config = demo_config(endpoint=plant_endpoint, storage="sqlite://")
    config.servers[0].publishing_interval_ms = 100
    storage = Storage(config.storage)
    storage.sync_tags(config.tags)
    hub = Hub(config)

    task = asyncio.create_task(watch_server(config.servers[0], hub))
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if len(hub.values) == len(config.tags):
                break
        assert hub.servers["demo-plant"]["status"] == "connected"
        assert set(hub.values) == {t.name for t in config.tags}
        assert hub.values["Pump1.Running"]["value"] == 1.0
        await flush(hub, storage)
        assert storage.sample_count() >= len(config.tags)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    client = TestClient(create_app(hub, storage))
    overview = client.get("/api/overview").json()
    assert len(overview["tags"]) == len(config.tags)
    history = client.get("/api/history", params={"tag": "Tank1.Level", "minutes": 5}).json()
    assert history["t"]


async def test_browse_finds_plant_folder(plant_endpoint):
    root = await browse(plant_endpoint)
    plant = next(n for n in root if n["name"] == "Plant")
    groups = await browse(plant_endpoint, plant["node"])
    tank = next(n for n in groups if n["name"] == "Tank1")
    leaves = await browse(plant_endpoint, tank["node"])
    level = next(n for n in leaves if n["name"] == "Level")
    assert level["class"] == "Variable"
    assert level["node"] == "ns=2;s=Plant.Tank1.Level"
    assert isinstance(level["value"], float)


async def test_unreachable_server_reports_disconnected():
    config = demo_config(endpoint=f"opc.tcp://127.0.0.1:{free_port()}", storage="sqlite://")
    hub = Hub(config)
    task = asyncio.create_task(watch_server(config.servers[0], hub, retry_max_s=0.2))
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if hub.servers["demo-plant"]["status"] == "disconnected":
                break
        assert hub.servers["demo-plant"]["status"] == "disconnected"
        assert any(e["kind"] == "warning" for e in hub.events)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
