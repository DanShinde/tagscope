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
