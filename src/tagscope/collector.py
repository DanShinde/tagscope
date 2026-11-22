"""OPC UA subscriptions and the background database writer."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from asyncua import Client, ua

from .config import Config, ServerConfig
from .hub import Hub
from .storage import BAD, GOOD, UNCERTAIN, Storage

log = logging.getLogger("tagscope")


def _quality(status: Any) -> int:
    try:
        if status is None or status.is_good():
            return GOOD
        if status.is_uncertain():
            return UNCERTAIN
    except AttributeError:  # pragma: no cover - defensive
        return GOOD
    return BAD


def _timestamp(data_value: Any) -> float:
    for attr in ("SourceTimestamp", "ServerTimestamp"):
        ts = getattr(data_value, attr, None)
        if ts is not None:
            return ts.timestamp()
    return time.time()


class _Handler:
    def __init__(self, hub: Hub, names: dict[str, str]) -> None:
        self.hub = hub
        self.names = names  # node id string -> tag name

    def datachange_notification(self, node, val, data) -> None:
        name = self.names.get(node.nodeid.to_string())
        if name is None:
            return
        dv = data.monitored_item.Value
        self.hub.update(name, val, _timestamp(dv), _quality(dv.StatusCode))

    def status_change_notification(self, status) -> None:  # pragma: no cover - server side
        log.warning("subscription status changed: %s", status)


async def _connect(server: ServerConfig) -> Client:
    client = Client(server.endpoint, timeout=5)
    if server.username:
        client.set_user(server.username)
    if server.password:
        client.set_password(server.password)
    await client.connect()
    return client


async def watch_server(server: ServerConfig, hub: Hub, retry_max_s: float = 30.0) -> None:
    """Subscribe to every tag of ``server`` and keep reconnecting forever."""
    delay = 1.0
    while True:
        client: Client | None = None
        try:
            hub.set_server_status(server.name, "connecting")
            client = await _connect(server)
            names: dict[str, str] = {}
            nodes = []
            for tag in server.tags:
                node = client.get_node(tag.node)
                names[node.nodeid.to_string()] = tag.name
                nodes.append(node)
            handler = _Handler(hub, names)
            sub = await client.create_subscription(server.publishing_interval_ms, handler)
            if nodes:
                results = await sub.subscribe_data_change(nodes, queuesize=10)
                for tag, res in zip(server.tags, results, strict=True):
                    if isinstance(res, ua.StatusCode):
                        hub.add_event("error", f"cannot subscribe {tag.name} ({tag.node}): {res.name}", tag.name)
            hub.set_server_status(server.name, "connected")
            delay = 1.0
            while True:
                await asyncio.sleep(2)
                await client.check_connection()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure means "reconnect"
            msg = str(exc) or exc.__class__.__name__
            log.warning("%s: %s, retrying in %.0fs", server.name, msg, delay)
            hub.set_server_status(server.name, "disconnected", msg[:200])
            for tag in server.tags:
                if tag.name in hub.values:
                    hub.values[tag.name]["quality"] = BAD
        finally:
            if client is not None:
                try:
                    await asyncio.wait_for(client.disconnect(), 3)
                except Exception:  # noqa: BLE001
                    pass
        await asyncio.sleep(delay)
        delay = min(delay * 2, retry_max_s)


async def _read_modbus_tag(client: Any, unit_id: int, spec: Any) -> Any:
    """Read one Modbus tag described by a :class:`ModbusTagSpec`."""
    if spec.table == "coil":
        result = await client.read_coils(spec.address, count=1, device_id=unit_id)
        if result.isError():
            raise ConnectionError(f"coil {spec.address} read failed: {result}")
        return bool(result.bits[0])
    if spec.table == "discrete":
        result = await client.read_discrete_inputs(spec.address, count=1, device_id=unit_id)
        if result.isError():
            raise ConnectionError(f"discrete {spec.address} read failed: {result}")
        return bool(result.bits[0])
    if spec.table == "input":
        result = await client.read_input_registers(spec.address, count=spec.register_count, device_id=unit_id)
    else:  # holding
        result = await client.read_holding_registers(spec.address, count=spec.register_count, device_id=unit_id)
    if result.isError():
        raise ConnectionError(f"{spec.table} {spec.address} read failed: {result}")
    registers = result.registers
    if spec.type == "bool":
        return bool(registers[0])
    if spec.type == "int16":
        return client.convert_from_registers(registers, data_type=client.DATATYPE.INT16)
    if spec.type == "uint16":
        return client.convert_from_registers(registers, data_type=client.DATATYPE.UINT16)
    if spec.type == "int32":
        return client.convert_from_registers(registers, data_type=client.DATATYPE.INT32)
    return client.convert_from_registers(registers, data_type=client.DATATYPE.FLOAT32)


async def watch_modbus(server: ServerConfig, hub: Hub, retry_max_s: float = 30.0) -> None:
    """Poll every tag of a Modbus TCP server and keep reconnecting forever.

    Mirrors :func:`watch_server`: the same status reporting, quality marking
    and exponential back-off, so the dashboard and storage see one pipeline.
    """
    try:
        from pymodbus.client import AsyncModbusTcpClient
    except ImportError:
        # Missing extra: fail once with a clear message instead of reconnecting
        # forever inside the retry loop.
        msg = "pymodbus is not installed — install tagscope[modbus] for Modbus servers"
        log.error("%s: %s", server.name, msg)
        hub.set_server_status(server.name, "error", msg)
        for tag in server.tags:
            if tag.name in hub.values:
                hub.values[tag.name]["quality"] = BAD
        return

    host, _, port_text = server.endpoint.removeprefix("modbus://").partition(":")
    port = int(port_text) if port_text else 502
    delay = 1.0
    while True:
        client: Any = None
        try:
            hub.set_server_status(server.name, "connecting")
            client = AsyncModbusTcpClient(host, port=port)
            if not await client.connect():
                raise ConnectionError(f"cannot connect to {host}:{port}")
            hub.set_server_status(server.name, "connected")
            delay = 1.0
            while True:
                for tag in server.tags:
                    if tag.modbus is None:
                        continue
                    value = await _read_modbus_tag(client, server.unit_id, tag.modbus)
                    hub.update(tag.name, value, time.time(), GOOD)
                await asyncio.sleep(server.poll_interval_ms / 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure means "reconnect"
            msg = str(exc) or exc.__class__.__name__
            log.warning("%s: %s, retrying in %.0fs", server.name, msg, delay)
            hub.set_server_status(server.name, "disconnected", msg[:200])
            for tag in server.tags:
                if tag.name in hub.values:
                    hub.values[tag.name]["quality"] = BAD
        finally:
            if client is not None:
                client.close()
        await asyncio.sleep(delay)
        delay = min(delay * 2, retry_max_s)


async def writer_loop(hub: Hub, storage: Storage, config: Config) -> None:
    """Flush buffered samples and events to the database periodically."""
    last_purge = 0.0
    try:
        while True:
            await asyncio.sleep(config.flush_interval_s)
            await flush(hub, storage)
            if config.retention_days and time.time() - last_purge > 3600:
                cutoff = time.time() - config.retention_days * 86400
                await asyncio.to_thread(storage.purge, cutoff)
                last_purge = time.time()
    finally:
        await flush(hub, storage)


async def flush(hub: Hub, storage: Storage) -> None:
    samples = hub.drain_samples()
    events = hub.drain_events()
    if samples:
        hub.stats["samples_written"] += await asyncio.to_thread(storage.write_samples, samples)
    for event in events:
        await asyncio.to_thread(storage.add_event, event["kind"], event["message"], event["tag"], event["ts"])


async def browse(endpoint: str, node_id: str | None = None, username: str | None = None, password: str | None = None):
    """List the children of a node (the Objects folder by default)."""
    server = ServerConfig(name="browse", endpoint=endpoint, username=username, password=password)
    client = await _connect(server)
    try:
        parent = client.get_node(node_id) if node_id else client.nodes.objects
        children = []
        for child in await parent.get_children():
            node_class = await child.read_node_class()
            name = (await child.read_browse_name()).Name
            item = {"node": child.nodeid.to_string(), "name": name, "class": node_class.name, "value": None}
            if node_class == ua.NodeClass.Variable:
                try:
                    value = await child.read_value()
                    item["value"] = value if isinstance(value, (int, float, str, bool)) else str(value)
                    item["type"] = (await child.read_data_type_as_variant_type()).name
                except Exception:  # noqa: BLE001
                    pass
            children.append(item)
        return children
    finally:
        await client.disconnect()
