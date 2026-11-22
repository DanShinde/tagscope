# Contributing to TagScope

Thanks for helping! Bug reports, docs fixes and new features are all welcome.
If you plan something bigger than a small fix, open an issue first so we can agree on the approach.

## Development setup

```bash
git clone https://github.com/DanShinde/tagscope && cd tagscope
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
tagscope demo                      # simulated plant + dashboard on http://localhost:8000
```

No PLC needed: the demo and the test suite both use the built-in OPC UA simulator
(`src/tagscope/simulator.py`).

## Tests and lint

```bash
pytest                             # ~5 s, includes end-to-end tests against the simulator
ruff check .
ruff format .
```

CI runs the same commands on Python 3.10 to 3.13 (Linux) and 3.12 (Windows). Please add or update
tests for every behaviour change.

## Project layout

| Path | What lives there |
|---|---|
| `src/tagscope/config.py` | YAML config model and validation |
| `src/tagscope/collector.py` | OPC UA subscriptions, reconnects, database writer loop, browsing |
| `src/tagscope/hub.py` | Live values, limits and alarm events, deadband, fan-out to WebSockets |
| `src/tagscope/storage.py` | SQLAlchemy tables, history downsampling, CSV export, retention |
| `src/tagscope/web.py` | FastAPI routes and the `/ws` feed |
| `src/tagscope/static/` | Dashboard: plain HTML, CSS and JS (no build step) |
| `src/tagscope/simulator.py` | Demo plant served over OPC UA |
| `tests/` | pytest suite; `tests/test_live.py` runs the full pipeline |

## Adding a new data source (Modbus, S7, MQTT…)

Sources only need to call `hub.update(tag_name, value, timestamp, quality)` for every new value and
`hub.set_server_status(name, status, error)` when the connection changes. Everything downstream
(deadband, alarms, storage, dashboard) then works automatically.

1. Add the config fields in `config.py` (for example a `protocol` key on a server) with validation and tests in `tests/test_config.py`.
2. Write an async `watch_<protocol>(server, hub)` function next to `watch_server` in `collector.py`, with the same reconnect-with-back-off behaviour.
3. Start it from `serve()` in `cli.py` based on the server's protocol.
4. Add an end-to-end test that runs a small in-process server for that protocol, like `tests/test_live.py` does for OPC UA.
5. Put any new dependency behind an optional extra in `pyproject.toml` (for example `tagscope[modbus]`).

## Dashboard changes

The UI is deliberately dependency-free apart from the bundled uPlot. Please check dark and light
themes and a narrow (phone) window, and keep colour for abnormal states only.

## Pull requests

- Keep PRs focused; one feature or fix per PR.
- Update `CHANGELOG.md` under an "Unreleased" heading.
- Describe how you tested it.

By contributing you agree that your work is released under the [MIT License](LICENSE).
