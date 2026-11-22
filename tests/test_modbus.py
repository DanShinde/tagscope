"""The Modbus TCP data source against a real in-process pymodbus server."""

import asyncio
import contextlib

import pytest

from tagscope.collector import watch_modbus
from tagscope.config import parse_config
from tagscope.hub import Hub
from tagscope.storage import GOOD

from .conftest import free_port


def modbus_config(port: int):
    return parse_config(
        {
            "storage": "sqlite://",
            "servers": [
                {
                    "name": "power-meter",
                    "protocol": "modbus",
                    "endpoint": f"modbus://127.0.0.1:{port}",
                    "unit_id": 1,
                    "poll_interval_ms": 50,
                    "tags": [
                        {"name": "Line/PowerKw", "node": "holding:1:float32", "unit": "kW", "high": 75},
                        {"name": "Line/Current", "node": "holding:3:uint16"},
                        {"name": "Line/Voltage", "node": "holding:4:int16"},
                        {"name": "Meter/Count", "node": "input:0:int32"},
                        {"name": "Breaker/Closed", "node": "coil:0:bool"},
                        {"name": "Meter/Ready", "node": "discrete:0:bool"},
                    ],
                }
            ],
        }
    )


@pytest.fixture
async def modbus_server():
    """A pymodbus TCP server on a free port with known register values."""
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import ModbusTcpServer

    port = free_port()
    math = AsyncModbusTcpClient("127.0.0.1")  # only for register conversion, never connected
    # A data block at address 1 holds PDU address 0 in values[0].
    hr_values = [0] * 16
    hr_values[1:3] = math.convert_to_registers(42.5, data_type=math.DATATYPE.FLOAT32)
    hr_values[3] = 1234
    hr_values[4] = math.convert_to_registers(-5, data_type=math.DATATYPE.INT16)[0]
    hr = ModbusSequentialDataBlock(1, hr_values)
    ir = ModbusSequentialDataBlock(1, math.convert_to_registers(-7, data_type=math.DATATYPE.INT32) + [0] * 4)
    co = ModbusSequentialDataBlock(1, [True, False])
    di = ModbusSequentialDataBlock(1, [True])
    context = ModbusServerContext(devices={1: ModbusDeviceContext(di=di, co=co, ir=ir, hr=hr)}, single=False)
    server = ModbusTcpServer(context=context, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if server.transport is not None:
            break
    yield port
    await server.shutdown()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_watch_modbus_polls_every_tag_into_the_hub(modbus_server):
    config = modbus_config(modbus_server)
    hub = Hub(config)
    task = asyncio.create_task(watch_modbus(config.servers[0], hub))
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if len(hub.values) == len(config.tags):
                break
        assert hub.servers["power-meter"]["status"] == "connected"
        assert set(hub.values) == {t.name for t in config.tags}
        assert hub.values["Line/PowerKw"]["value"] == pytest.approx(42.5)
        assert hub.values["Line/Current"]["value"] == 1234
        assert hub.values["Line/Voltage"]["value"] == -5
        assert hub.values["Meter/Count"]["value"] == -7
        assert hub.values["Breaker/Closed"]["value"] == 1.0  # bools arrive as 1/0, like OPC UA
        assert hub.values["Meter/Ready"]["value"] == 1.0
        assert all(v["quality"] == GOOD for v in hub.values.values())
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_watch_modbus_picks_up_changed_registers(modbus_server):
    config = modbus_config(modbus_server)
    hub = Hub(config)
    task = asyncio.create_task(watch_modbus(config.servers[0], hub))
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if "Line/Current" in hub.values:
                break
        assert hub.values["Line/Current"]["value"] == 1234
        # A client writes a new current; the next poll sees it.
        from pymodbus.client import AsyncModbusTcpClient

        writer = AsyncModbusTcpClient("127.0.0.1", port=modbus_server)
        assert await writer.connect()
        await writer.write_register(3, 999, device_id=1)
        writer.close()
        for _ in range(100):
            await asyncio.sleep(0.1)
            if hub.values["Line/Current"]["value"] == 999:
                break
        assert hub.values["Line/Current"]["value"] == 999
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_watch_modbus_reports_disconnected_when_the_server_is_down():
    config = modbus_config(free_port())  # nothing listens there
    hub = Hub(config)
    task = asyncio.create_task(watch_modbus(config.servers[0], hub))
    try:
        for _ in range(50):
            await asyncio.sleep(0.1)
            if hub.servers["power-meter"]["status"] == "disconnected":
                break
        assert hub.servers["power-meter"]["status"] == "disconnected"
        assert hub.servers["power-meter"]["error"]
        assert hub.values == {}
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
