# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Modbus TCP data source: `protocol: modbus` servers with `unit_id` and `poll_interval_ms`,
  tag nodes as `<table>:<address>:<type>` (`coil`/`discrete`/`input`/`holding`,
  `bool`/`int16`/`uint16`/`int32`/`float32`), `watch_modbus` in the collector with the same
  reconnect-with-back-off as OPC UA, and a `tagscope[modbus]` extra (pymodbus). Config parsing is
  covered in `tests/test_config.py`, live polling against an in-process pymodbus server in
  `tests/test_modbus.py`.
- CONTRIBUTING.md and issue templates.

## [0.1.0] - 2026-11-22

### Added
- OPC UA subscriptions for any number of servers, with automatic reconnect and back-off.
- Storage in SQLite (zero config) or PostgreSQL / TimescaleDB through SQLAlchemy.
- Per-tag deadband, high/low limits with alarm and clear events, retention clean-up.
- Live dashboard: tag list with search, value cards with sparklines and limit bars,
  multi-tag zoomable trends (5 min to 24 h, server-side downsampling), event log,
  address space browser, CSV export, dark and light themes.
- REST API (`/api/docs`) and a WebSocket feed (`/ws`).
- CLI: `tagscope demo`, `run`, `init`, `browse`, `export`.
- Built-in simulated plant so anyone can try it without hardware.
