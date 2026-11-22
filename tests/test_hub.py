from tagscope.hub import Hub, limit_state
from tagscope.storage import BAD


def test_limit_state(config):
    tag = config.tags[0]
    assert limit_state(tag, 50) == "normal"
    assert limit_state(tag, 90) == "high"
    assert limit_state(tag, 5) == "low"
    assert limit_state(tag, None) == "unknown"


def test_alarm_and_clear_events(config):
    hub = Hub(config)
    hub.update("level", 50, ts=1)
    hub.update("level", 95, ts=2)
    hub.update("level", 96, ts=3)
    hub.update("level", 40, ts=4)
    kinds = [(e["kind"], e["ts"]) for e in hub.drain_events()]
    assert kinds == [("alarm", 2), ("cleared", 4)]
    assert hub.values["level"]["state"] == "normal"


def test_deadband_filters_stored_samples(config):
    hub = Hub(config)
    for i, v in enumerate([100, 102, 104, 106, 107, 112]):
        hub.update("speed", v, ts=i)
    assert [s.value for s in hub.drain_samples()] == [100, 106, 112]
    assert hub.values["speed"]["value"] == 112  # live value is never filtered


def test_bools_strings_and_unknown_tags(config):
    hub = Hub(config)
    hub.update("running", True, ts=1)
    hub.update("running", "oops", ts=2)
    hub.update("not-configured", 1, ts=3)
    samples = hub.drain_samples()
    assert [(s.value, s.quality) for s in samples] == [(1.0, 0), (None, BAD)]
    assert "not-configured" not in hub.values


def test_broadcast_to_listeners(config):
    hub = Hub(config)
    queue = hub.subscribe()
    hub.update("level", 20, ts=1)
    msg = queue.get_nowait()
    assert msg["type"] == "value" and msg["tag"] == "level" and msg["value"] == 20
    hub.unsubscribe(queue)
    hub.update("level", 21, ts=2)
    assert queue.empty()


def test_slow_listener_drops_oldest(config):
    hub = Hub(config)
    queue = hub.subscribe()
    for i in range(1100):
        hub.update("level", 50 + (i % 2), ts=i)
    assert queue.qsize() == 1000
    assert queue.get_nowait()["ts"] == 100


def test_server_status_events(config):
    hub = Hub(config)
    hub.set_server_status("plc", "connected")
    hub.set_server_status("plc", "connected")  # no duplicate
    hub.set_server_status("plc", "disconnected", "timeout")
    events = hub.drain_events()
    assert [e["kind"] for e in events] == ["info", "warning"]
    assert "timeout" in events[1]["message"]
