"""A small simulated plant served over OPC UA, used by ``tagscope demo``.

The process: a tank is filled by a feed valve and emptied by a pump. A
heater keeps a reactor at temperature, and a filler counts bottles. Every
few minutes the pump trips, so the level climbs into alarm and recovers,
which makes the demo dashboard come alive.
"""

from __future__ import annotations

import asyncio
import math
import random
import time

from asyncua import Server, ua

from .config import Config, ServerConfig, TagConfig, WebConfig

NAMESPACE = "urn:tagscope:demo-plant"
DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4841/tagscope/demo"

#: (path, unit, description, low, high, deadband, initial value)
SIGNALS: list[tuple[str, str, str, float | None, float | None, float, float | bool]] = [
    ("Tank1.Level", "%", "Buffer tank level", 20, 80, 0.05, 50.0),
    ("Tank1.InletFlow", "m³/h", "Feed flow into the tank", None, None, 0.05, 12.0),
    ("Tank1.OutletFlow", "m³/h", "Pump discharge flow", None, None, 0.05, 12.0),
    ("Pump1.Running", "", "Transfer pump running", None, None, 0, True),
    ("Pump1.Speed", "rpm", "Transfer pump speed", None, 1720, 1, 1200.0),
    ("Pump1.Current", "A", "Motor current", None, 38, 0.05, 24.0),
    ("Reactor.Temperature", "°C", "Reactor temperature", 70, 90, 0.05, 80.0),
    ("Reactor.Pressure", "bar", "Reactor pressure", None, 6.5, 0.01, 4.2),
    ("Filler.BottleCount", "pcs", "Bottles filled since start", None, None, 0, 0.0),
    ("Line.PowerKw", "kW", "Line power consumption", None, None, 0.1, 42.0),
]


def demo_config(endpoint: str = DEFAULT_ENDPOINT, storage: str = "sqlite:///tagscope-demo.db") -> Config:
    """The configuration ``tagscope demo`` uses to log the simulated plant."""
    tags = [
        TagConfig(
            name=path,
            node=f"ns=2;s=Plant.{path}",
            unit=unit,
            description=desc,
            low=low,
            high=high,
            deadband=deadband,
            server="demo-plant",
        )
        for path, unit, desc, low, high, deadband, _ in SIGNALS
    ]
    server = ServerConfig(name="demo-plant", endpoint=endpoint, publishing_interval_ms=250, tags=tags)
    return Config(storage=storage, retention_days=7, web=WebConfig(), servers=[server])


class Plant:
    """Deterministic-ish process model, stepped at a fixed rate."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.state: dict[str, float | bool] = {path: init for path, *_, init in SIGNALS}
        self.t = 0.0
        self.trip_until = 0.0
        self.next_trip = 45.0
        self._bottles = 0.0

    def step(self, dt: float) -> dict[str, float | bool]:
        self.t += dt
        s, rng = self.state, self.rng

        # Pump trips now and then; it restarts automatically after a while.
        if self.t >= self.next_trip and self.t >= self.trip_until:
            self.trip_until = self.t + rng.uniform(30, 45)
            self.next_trip = self.trip_until + rng.uniform(90, 180)
        running = self.t >= self.trip_until

        inlet = 12 + 2.5 * math.sin(self.t / 23) + rng.gauss(0, 0.25)
        level = float(s["Tank1.Level"])
        target_speed = min(1750.0, max(600.0, 1200 + (level - 50) * 25)) if running else 0.0
        speed = float(s["Pump1.Speed"]) + (target_speed - float(s["Pump1.Speed"])) * min(1.0, dt * 1.5)
        outlet = max(0.0, speed / 1200 * 12 + rng.gauss(0, 0.2)) if speed > 50 else 0.0
        level = min(100.0, max(0.0, level + (inlet - outlet) * dt * 0.05))
        current = 4 + speed / 1200 * 18 + rng.gauss(0, 0.3) if speed > 50 else 0.0

        temp = float(s["Reactor.Temperature"])
        setpoint = 80 + 4 * math.sin(self.t / 70)
        temp += (setpoint - temp) * dt * 0.08 + rng.gauss(0, 0.06)
        pressure = 2.2 + (temp - 20) * 0.033 + rng.gauss(0, 0.02)

        if running:
            self._bottles += dt * (outlet / 12) * 4.2
        power = 18 + current * 0.9 + (temp - 20) * 0.12 + rng.gauss(0, 0.4)

        s.update(
            {
                "Tank1.Level": round(level, 2),
                "Tank1.InletFlow": round(inlet, 2),
                "Tank1.OutletFlow": round(outlet, 2),
                "Pump1.Running": running,
                "Pump1.Speed": round(speed, 0),
                "Pump1.Current": round(max(0.0, current), 2),
                "Reactor.Temperature": round(temp, 2),
                "Reactor.Pressure": round(pressure, 3),
                "Filler.BottleCount": float(int(self._bottles)),
                "Line.PowerKw": round(power, 1),
            }
        )
        return dict(s)


def backfill(storage, config: Config, plant: Plant, minutes: float = 30, rate_hz: float = 1.0) -> int:
    """Fill an empty demo database with ``minutes`` of simulated history.

    This way the dashboard shows full trends the first time it opens.
    """
    if storage.sample_count():
        return 0
    from .hub import Hub

    hub = Hub(config)
    by_path = {tag.node.removeprefix("ns=2;s=Plant."): tag.name for tag in config.tags}
    now = time.time()
    steps = int(minutes * 60 * rate_hz)
    written = 0
    for i in range(steps):
        ts = now - (steps - i) / rate_hz
        for path, value in plant.step(1.0 / rate_hz).items():
            if path in by_path:
                hub.update(by_path[path], value, ts)
        if i % 300 == 0:
            written += storage.write_samples(hub.drain_samples())
    written += storage.write_samples(hub.drain_samples())
    for event in hub.drain_events():
        storage.add_event(event["kind"], event["message"], event["tag"], event["ts"])
    return written


async def run_simulator(
    endpoint: str = DEFAULT_ENDPOINT,
    rate_hz: float = 4.0,
    plant: Plant | None = None,
    ready: asyncio.Event | None = None,
) -> None:
    """Serve the simulated plant until cancelled."""
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("TagScope demo plant")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace(NAMESPACE)

    plant_obj = await server.nodes.objects.add_folder(ua.NodeId("Plant", idx), "Plant")
    groups: dict[str, object] = {}
    variables = {}
    for path, *_meta, init in SIGNALS:
        group, leaf = path.split(".")
        if group not in groups:
            groups[group] = await plant_obj.add_object(ua.NodeId(f"Plant.{group}", idx), group)
        vtype = ua.VariantType.Boolean if isinstance(init, bool) else ua.VariantType.Double
        var = await groups[group].add_variable(ua.NodeId(f"Plant.{path}", idx), leaf, ua.Variant(init, vtype))
        variables[path] = (var, vtype)

    plant = plant or Plant()
    dt = 1.0 / rate_hz
    async with server:
        if ready is not None:
            ready.set()
        next_tick = time.monotonic()
        while True:
            values = plant.step(dt)
            for path, (var, vtype) in variables.items():
                await var.write_value(ua.Variant(values[path], vtype))
            next_tick += dt
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
