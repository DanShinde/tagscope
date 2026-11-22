"""Configuration model and YAML loader."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


PROTOCOLS = ("opcua", "modbus")
MODBUS_TABLES = ("coil", "discrete", "input", "holding")
MODBUS_TYPES = ("bool", "int16", "uint16", "int32", "float32")


@dataclass
class ModbusTagSpec:
    """A parsed Modbus node string: ``<table>:<address>:<type>``."""

    table: str
    address: int
    type: str

    @property
    def register_count(self) -> int:
        """How many 16-bit registers the type occupies (bits take one read)."""
        return 1 if self.type in ("bool", "int16", "uint16") else 2


def parse_modbus_node(node: str, where: str) -> ModbusTagSpec:
    """Parse and validate a Modbus node string such as ``holding:0:float32``."""
    parts = node.split(":")
    if len(parts) != 3:
        raise ConfigError(
            f"{where}: Modbus node {node!r} must be <table>:<address>:<type>, e.g. 'holding:0:float32'"
        )
    table, address_text, type_name = parts
    if table not in MODBUS_TABLES:
        raise ConfigError(f"{where}: unknown Modbus table {table!r} in {node!r} (coil, discrete, input, holding)")
    if type_name not in MODBUS_TYPES:
        raise ConfigError(
            f"{where}: unknown Modbus type {type_name!r} in {node!r} (bool, int16, uint16, int32, float32)"
        )
    try:
        address = int(address_text)
    except ValueError:
        raise ConfigError(f"{where}: Modbus address in {node!r} must be a number") from None
    if address < 0:
        raise ConfigError(f"{where}: Modbus address in {node!r} must be >= 0")
    return ModbusTagSpec(table=table, address=address, type=type_name)


@dataclass
class TagConfig:
    name: str
    node: str
    unit: str = ""
    description: str = ""
    low: float | None = None
    high: float | None = None
    deadband: float = 0.0
    server: str = ""
    modbus: ModbusTagSpec | None = None  # set for servers with protocol: modbus


@dataclass
class ServerConfig:
    name: str
    endpoint: str
    username: str | None = None
    password: str | None = None
    publishing_interval_ms: int = 500
    tags: list[TagConfig] = field(default_factory=list)
    protocol: str = "opcua"
    unit_id: int = 1
    poll_interval_ms: int = 1000


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class Config:
    storage: str = "sqlite:///tagscope.db"
    retention_days: float | None = 30
    flush_interval_s: float = 1.0
    web: WebConfig = field(default_factory=WebConfig)
    servers: list[ServerConfig] = field(default_factory=list)

    @property
    def tags(self) -> list[TagConfig]:
        return [tag for server in self.servers for tag in server.tags]

    def tag(self, name: str) -> TagConfig | None:
        return next((t for t in self.tags if t.name == name), None)


def _expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` and ``${VAR:-default}`` inside strings."""
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name!r} is not set")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _opt_float(value: Any, where: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: expected a number, got {value!r}") from exc


def parse_config(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from a plain dictionary (e.g. parsed YAML)."""
    if not isinstance(data, dict):
        raise ConfigError("configuration must be a mapping")
    data = _expand_env(data)

    web_data = data.get("web") or {}
    web = WebConfig(host=str(web_data.get("host", "127.0.0.1")), port=int(web_data.get("port", 8000)))

    servers: list[ServerConfig] = []
    seen_tags: set[str] = set()
    for i, raw in enumerate(data.get("servers") or []):
        where = f"servers[{i}]"
        if "endpoint" not in raw:
            raise ConfigError(f"{where}: 'endpoint' is required")
        protocol = str(raw.get("protocol", "opcua"))
        if protocol not in PROTOCOLS:
            raise ConfigError(f"{where}: unknown protocol {protocol!r} (opcua, modbus)")
        endpoint = str(raw["endpoint"])
        if protocol == "modbus" and not endpoint.startswith("modbus://"):
            raise ConfigError(f"{where}: a Modbus server's endpoint must start with modbus://, got {endpoint!r}")
        unit_id = int(raw.get("unit_id", 1))
        if not 0 <= unit_id <= 255:
            raise ConfigError(f"{where}: unit_id must be in 0-255, got {unit_id}")
        poll_interval_ms = int(raw.get("poll_interval_ms", 1000))
        if poll_interval_ms <= 0:
            raise ConfigError(f"{where}: poll_interval_ms must be > 0, got {poll_interval_ms}")
        server = ServerConfig(
            name=str(raw.get("name") or f"server{i + 1}"),
            endpoint=endpoint,
            username=raw.get("username"),
            password=raw.get("password"),
            publishing_interval_ms=int(raw.get("publishing_interval_ms", 500)),
            protocol=protocol,
            unit_id=unit_id,
            poll_interval_ms=poll_interval_ms,
        )
        for j, traw in enumerate(raw.get("tags") or []):
            twhere = f"{where}.tags[{j}]"
            if isinstance(traw, str):
                traw = {"node": traw}
            if "node" not in traw:
                raise ConfigError(f"{twhere}: 'node' is required")
            name = str(traw.get("name") or traw["node"])
            if name in seen_tags:
                raise ConfigError(f"{twhere}: duplicate tag name {name!r}")
            seen_tags.add(name)
            spec = parse_modbus_node(str(traw["node"]), twhere) if protocol == "modbus" else None
            server.tags.append(
                TagConfig(
                    name=name,
                    node=str(traw["node"]),
                    unit=str(traw.get("unit", "")),
                    description=str(traw.get("description", "")),
                    low=_opt_float(traw.get("low"), f"{twhere}.low"),
                    high=_opt_float(traw.get("high"), f"{twhere}.high"),
                    deadband=_opt_float(traw.get("deadband"), f"{twhere}.deadband") or 0.0,
                    server=server.name,
                    modbus=spec,
                )
            )
        servers.append(server)

    if len({s.name for s in servers}) != len(servers):
        raise ConfigError("server names must be unique")

    retention = data.get("retention_days", 30)
    return Config(
        storage=str(data.get("storage", "sqlite:///tagscope.db")),
        retention_days=_opt_float(retention, "retention_days"),
        flush_interval_s=float(data.get("flush_interval_s", 1.0)),
        web=web,
        servers=servers,
    )


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML configuration file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return parse_config(data)
