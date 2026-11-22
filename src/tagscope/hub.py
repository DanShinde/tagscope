"""Live state shared between the collector, the database writer and the UI."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import Config, TagConfig
from .storage import BAD, GOOD, Sample


def limit_state(tag: TagConfig, value: float | None) -> str:
    """Classify a value against its configured limits."""
    if value is None:
        return "unknown"
    if tag.high is not None and value >= tag.high:
        return "high"
    if tag.low is not None and value <= tag.low:
        return "low"
    return "normal"


class Hub:
    """Keeps the latest value of every tag and fans updates out to listeners.

    Listeners (WebSocket clients) receive small JSON-able dictionaries via an
    :class:`asyncio.Queue`. Slow listeners never block the collector: when a
    queue is full the oldest message is dropped.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.tags: dict[str, TagConfig] = {t.name: t for t in config.tags}
        self.values: dict[str, dict[str, Any]] = {}
        self.servers: dict[str, dict[str, Any]] = {
            s.name: {"name": s.name, "endpoint": s.endpoint, "status": "connecting", "error": "", "since": time.time()}
            for s in config.servers
        }
        self.stats = {"samples_written": 0, "started": time.time()}
        self._listeners: set[asyncio.Queue] = set()
        self._pending: list[Sample] = []
        self._last_stored: dict[str, float | None] = {}
        self.events: list[dict] = []

    # -- listeners ----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def broadcast(self, message: dict[str, Any]) -> None:
        for queue in list(self._listeners):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race
                    pass
            queue.put_nowait(message)

    # -- updates ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "values": self.values,
            "servers": list(self.servers.values()),
            "stats": self.stats,
        }

    def set_server_status(self, name: str, status: str, error: str = "") -> None:
        server = self.servers.setdefault(name, {"name": name, "endpoint": ""})
        if server.get("status") == status and server.get("error") == error:
            return
        server.update(status=status, error=error, since=time.time())
        self.broadcast({"type": "server", "server": server})
        kind = {"connected": "info", "disconnected": "warning", "error": "error"}.get(status)
        if kind:
            text = f"{name}: {status}" + (f" ({error})" if error else "")
            self.add_event(kind, text)

    def add_event(self, kind: str, message: str, tag: str = "", ts: float | None = None) -> None:
        event = {"ts": ts or time.time(), "tag": tag, "kind": kind, "message": message}
        self.events.append(event)
        self.broadcast({"type": "event", "event": event})

    def drain_events(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    def update(self, name: str, value: Any, ts: float | None = None, quality: int = GOOD) -> None:
        """Record a new value coming from a server."""
        tag = self.tags.get(name)
        if tag is None:
            return
        ts = ts or time.time()
        num = _to_float(value)
        if num is None and value is not None:
            quality = max(quality, BAD)
        state = limit_state(tag, num)

        previous = self.values.get(name)
        prev_state = previous["state"] if previous else "normal"
        if state != prev_state and prev_state != "unknown" and state != "unknown":
            if state == "normal":
                self.add_event("cleared", f"{name} back to normal ({_fmt(num)} {tag.unit})".rstrip(), name, ts)
            else:
                limit = tag.high if state == "high" else tag.low
                self.add_event(
                    "alarm",
                    f"{name} {state.upper()} {_fmt(num)} {tag.unit} (limit {_fmt(limit)})".replace("  ", " "),
                    name,
                    ts,
                )

        entry = {"value": num, "raw": value if num is None else None, "ts": ts, "quality": quality, "state": state}
        self.values[name] = entry
        self.broadcast({"type": "value", "tag": name, **entry})

        last = self._last_stored.get(name, _MISSING)
        if (
            last is _MISSING or num is None or last is None or tag.deadband <= 0 or abs(num - last) >= tag.deadband  # type: ignore[operator]
        ):
            self._pending.append(Sample(name, ts, num, quality))
            self._last_stored[name] = num

    def drain_samples(self) -> list[Sample]:
        samples, self._pending = self._pending, []
        return samples


_MISSING = object()


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".")
