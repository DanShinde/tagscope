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


@dataclass
class ServerConfig:
    name: str
    endpoint: str
    username: str | None = None
    password: str | None = None
    publishing_interval_ms: int = 500
    tags: list[TagConfig] = field(default_factory=list)


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
        server = ServerConfig(
            name=str(raw.get("name") or f"server{i + 1}"),
            endpoint=str(raw["endpoint"]),
            username=raw.get("username"),
            password=raw.get("password"),
            publishing_interval_ms=int(raw.get("publishing_interval_ms", 500)),
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
