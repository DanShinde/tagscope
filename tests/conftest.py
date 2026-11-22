import socket

import pytest

from tagscope.config import parse_config
from tagscope.storage import Storage


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def config():
    return parse_config(
        {
            "storage": "sqlite://",
            "servers": [
                {
                    "name": "plc",
                    "endpoint": "opc.tcp://127.0.0.1:4840",
                    "tags": [
                        {"name": "level", "node": "ns=2;s=Level", "unit": "%", "low": 10, "high": 90},
                        {"name": "speed", "node": "ns=2;s=Speed", "deadband": 5},
                        {"name": "running", "node": "ns=2;s=Running"},
                    ],
                }
            ],
        }
    )


@pytest.fixture
def storage(config):
    s = Storage("sqlite://")
    s.sync_tags(config.tags)
    yield s
    s.close()
