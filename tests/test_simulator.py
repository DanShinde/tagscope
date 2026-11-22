from tagscope.simulator import SIGNALS, Plant, backfill, demo_config
from tagscope.storage import Storage


def test_plant_stays_in_physical_range():
    plant = Plant(seed=1)
    trips = 0
    running = True
    for _ in range(3600):
        s = plant.step(1.0)
        assert 0 <= s["Tank1.Level"] <= 100
        assert s["Tank1.OutletFlow"] >= 0
        if running and not s["Pump1.Running"]:
            trips += 1
        running = s["Pump1.Running"]
    assert trips >= 5  # the demo needs some action
    assert set(s) == {path for path, *_ in SIGNALS}


def test_backfill_only_fills_empty_database():
    config = demo_config(storage="sqlite://")
    storage = Storage(config.storage)
    storage.sync_tags(config.tags)
    written = backfill(storage, config, Plant(seed=3), minutes=5)
    assert written > 0
    assert storage.sample_count() == written
    assert backfill(storage, config, Plant(seed=3), minutes=5) == 0
