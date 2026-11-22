import pytest

from tagscope.config import ConfigError, load_config, parse_config


def test_defaults_and_short_tag_form():
    cfg = parse_config({"servers": [{"endpoint": "opc.tcp://x:4840", "tags": ["ns=2;s=A"]}]})
    assert cfg.storage == "sqlite:///tagscope.db"
    assert cfg.web.port == 8000
    server = cfg.servers[0]
    assert server.name == "server1"
    assert server.tags[0].name == "ns=2;s=A"
    assert server.tags[0].server == "server1"


def test_env_substitution(monkeypatch):
    monkeypatch.setenv("PLC_PW", "s3cret")
    cfg = parse_config(
        {
            "storage": "${DB_URL:-sqlite:///x.db}",
            "servers": [{"endpoint": "opc.tcp://x", "password": "${PLC_PW}"}],
        }
    )
    assert cfg.servers[0].password == "s3cret"
    assert cfg.storage == "sqlite:///x.db"


def test_missing_env_var_is_an_error(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(ConfigError, match="NOPE"):
        parse_config({"storage": "${NOPE}"})


@pytest.mark.parametrize(
    "data, message",
    [
        ({"servers": [{"name": "a"}]}, "endpoint"),
        ({"servers": [{"endpoint": "e", "tags": [{"name": "x"}]}]}, "node"),
        (
            {"servers": [{"endpoint": "e", "tags": [{"node": "a", "name": "x"}, {"node": "b", "name": "x"}]}]},
            "duplicate",
        ),
        ({"servers": [{"endpoint": "e", "tags": [{"node": "a", "high": "lots"}]}]}, "number"),
        ({"servers": [{"name": "s", "endpoint": "e"}, {"name": "s", "endpoint": "f"}]}, "unique"),
    ],
)
def test_invalid_configs(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_load_example_config(tmp_path):
    from tagscope.cli import EXAMPLE_CONFIG

    path = tmp_path / "tagscope.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    cfg = load_config(path)
    assert [t.name for t in cfg.tags] == ["Tank level", "Pump running", 'ns=3;s="DB_Process"."BatchCounter"']
    assert cfg.tags[0].high == 90
    assert cfg.tags[0].deadband == 0.2


def test_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("servers: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_modbus_server_config_parses():
    cfg = parse_config(
        {
            "servers": [
                {
                    "name": "power-meter",
                    "protocol": "modbus",
                    "endpoint": "modbus://192.168.0.50:502",
                    "unit_id": 1,
                    "poll_interval_ms": 1000,
                    "tags": [
                        {"name": "Line/PowerKw", "node": "holding:40001:float32", "unit": "kW", "high": 75},
                        {"name": "Breaker/Closed", "node": "coil:0:bool"},
                    ],
                }
            ]
        }
    )
    server = cfg.servers[0]
    assert server.protocol == "modbus"
    assert server.unit_id == 1
    assert server.poll_interval_ms == 1000
    power = server.tags[0].modbus
    assert power is not None
    assert (power.table, power.address, power.type) == ("holding", 40001, "float32")
    assert power.register_count == 2
    coil = server.tags[1].modbus
    assert coil is not None and coil.register_count == 1


def test_opcua_server_defaults_and_no_modbus_spec():
    cfg = parse_config({"servers": [{"endpoint": "opc.tcp://x:4840", "tags": ["ns=2;s=A"]}]})
    server = cfg.servers[0]
    assert server.protocol == "opcua"
    assert server.unit_id == 1
    assert server.poll_interval_ms == 1000
    assert server.tags[0].modbus is None


@pytest.mark.parametrize(
    "data, message",
    [
        ({"protocol": "s7"}, "unknown protocol"),
        ({"protocol": "modbus", "endpoint": "opc.tcp://x:4840"}, "must start with modbus://"),
        ({"protocol": "modbus", "endpoint": "modbus://x:502", "unit_id": 256}, "unit_id"),
        ({"protocol": "modbus", "endpoint": "modbus://x:502", "unit_id": -1}, "unit_id"),
        ({"protocol": "modbus", "endpoint": "modbus://x:502", "poll_interval_ms": 0}, "poll_interval_ms"),
        (
            {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:1"}]},
            "<table>:<address>:<type>",
        ),
        (
            {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:1:float32:extra"}]},
            "<table>:<address>:<type>",
        ),
        (
            {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "queue:1:bool"}]},
            "unknown Modbus table",
        ),
        (
            {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:1:string"}]},
            "unknown Modbus type",
        ),
        (
            {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:x:bool"}]},
            "must be a number",
        ),
        ({"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:-1:bool"}]}, "must be >= 0"),
    ],
    ids=[
        "protocol",
        "endpoint-scheme",
        "unit-too-big",
        "unit-negative",
        "poll-zero",
        "two-parts",
        "four-parts",
        "bad-table",
        "bad-type",
        "addr-nan",
        "addr-negative",
    ],
)
def test_invalid_modbus_configs(data, message):
    server = {"protocol": "modbus", "endpoint": "modbus://x:502", "tags": [{"node": "holding:1:bool"}]}
    server.update(data)
    with pytest.raises(ConfigError, match=message):
        parse_config({"servers": [server]})


def test_parse_modbus_node_directly():
    from tagscope.config import parse_modbus_node

    spec = parse_modbus_node("input:2:float32", "t")
    assert (spec.table, spec.address, spec.type, spec.register_count) == ("input", 2, "float32", 2)
    with pytest.raises(ConfigError, match="unknown Modbus table"):
        parse_modbus_node("nope:1:bool", "t")
