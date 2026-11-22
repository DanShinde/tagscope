<div align="center">

# TagScope

**Log OPC UA tags to a database and watch them live in your browser.**

A lightweight historian for PLCs and industrial equipment: subscribe to any OPC UA server
(Siemens S7-1500, Beckhoff, B&R, Kepware, Ignition, Prosys…), store every change in SQLite or
PostgreSQL, and get a fast, modern dashboard with live values, trends, alarms and CSV export.
One `pip install`, one YAML file, no cloud.

[![CI](https://github.com/DanShinde/tagscope/actions/workflows/ci.yml/badge.svg)](https://github.com/DanShinde/tagscope/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tagscope.svg)](https://pypi.org/project/tagscope/)
[![Python](https://img.shields.io/pypi/pyversions/tagscope.svg)](https://pypi.org/project/tagscope/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<img src="docs/demo.gif" alt="TagScope dashboard showing live tag cards, a multi-tag trend and the OPC UA address space browser" width="900">

</div>

## Try it in 10 seconds

```bash
pip install tagscope
tagscope demo
```

`demo` starts a small simulated plant (a buffer tank, a transfer pump that trips now and then,
a reactor and a filler) as an OPC UA server, logs it to `tagscope-demo.db` and opens the dashboard
at <http://localhost:8000>. It even pre-fills 30 minutes of history, so the trends look alive
from the first second.

## Why

Getting PLC data into a database usually means a commercial historian, a Node-RED flow you have
to babysit, or a one-off script without a UI. TagScope sits in between:

- **Small**: a single Python process, no broker, no separate front-end build.
- **Honest storage**: plain SQL tables you can query from Grafana, Power BI, Excel or pandas.
- **Made for the plant floor**: limits and alarms, deadbands, reconnects that just work,
  and a dashboard that follows ISA-101 ideas (calm grey, colour only when something is wrong).

## Features

| | |
|---|---|
| **Collect** | OPC UA subscriptions (not polling) for many servers at once, username/password login, automatic reconnect with back-off, per-tag deadband |
| **Store** | SQLite out of the box, PostgreSQL / TimescaleDB via `tagscope[postgres]`, retention clean-up, quality flag per sample |
| **See** | Live value cards with sparklines and limit bars, multi-tag trends with zoom (5 min to 24 h, downsampled server side), event log, dark and light themes, works on a phone |
| **Alarm** | High / low limits per tag, alarm and clear events with timestamps, active alarm counter |
| **Explore** | Built-in address space browser: click through the server and copy node ids into your config |
| **Export** | One click CSV from the dashboard, `tagscope export` from the shell, REST API with OpenAPI docs at `/api/docs`, WebSocket feed at `/ws` |

## Use it with your PLC

```bash
tagscope init                                   # writes an example tagscope.yaml
tagscope browse opc.tcp://192.168.0.1:4840      # find the node ids you need
tagscope run tagscope.yaml --open
```

```yaml
storage: sqlite:///line1.db          # or postgresql+psycopg://user:${DB_PASSWORD}@host/db
retention_days: 90

servers:
  - name: line1-plc
    endpoint: opc.tcp://192.168.0.1:4840
    username: ${PLC_USER:-}            # values can come from the environment
    password: ${PLC_PASSWORD:-}
    publishing_interval_ms: 500
    tags:
      - name: Mixer/Temperature
        node: ns=3;s="DB_Mixer"."Temperature"
        unit: °C
        low: 20
        high: 85
        deadband: 0.1                  # store only changes bigger than this
      - name: Mixer/Running
        node: ns=3;s="DB_Mixer"."Running"
      - ns=3;s="DB_Counters"."Good"   # short form: the node id is the name
```

More in [`examples/`](examples), including a Siemens S7-1500 setup and PostgreSQL.

> **Siemens tip:** enable the OPC UA server in the PLC properties in TIA Portal and tick
> *Accessible from HMI/OPC UA* on the data blocks you want to log. Node ids then look like
> `ns=3;s="DB_Name"."Member"`.

## Command line

| Command | What it does |
|---|---|
| `tagscope demo` | Simulated plant + logger + dashboard, nothing to configure |
| `tagscope run CONFIG` | Log the servers and tags in a YAML file and serve the dashboard |
| `tagscope init [PATH]` | Write an example config |
| `tagscope browse ENDPOINT [NODE]` | List the nodes of a server, with current values |
| `tagscope export CONFIG --minutes 60 -o out.csv` | Export logged samples |

## Data model

Three tables, easy to query from anything that speaks SQL:

```sql
SELECT t.name, s.ts, s.value
FROM tagscope_samples s JOIN tagscope_tags t ON t.id = s.tag_id
WHERE t.name = 'Mixer/Temperature' AND s.ts > CAST(strftime('%s', 'now', '-1 hour') AS REAL);
```

| Table | Columns |
|---|---|
| `tagscope_tags` | `id, name, server, node, unit, description, low, high` |
| `tagscope_samples` | `tag_id, ts (epoch seconds), value, quality (0 good, 1 uncertain, 2 bad)` |
| `tagscope_events` | `id, ts, tag, kind (alarm, cleared, info, warning, error), message` |

## How it works

```mermaid
flowchart LR
    PLC1[PLC / OPC UA server] -- subscriptions --> C[Collector]
    PLC2[More servers] -- subscriptions --> C
    C --> H[(Live state + alarms)]
    H -- batched writes --> DB[(SQLite / PostgreSQL)]
    H -- WebSocket --> UI[Dashboard]
    DB -- history, CSV --> API[REST API] --> UI
```

Everything runs on one asyncio event loop: asyncua subscriptions feed an in-memory hub,
which applies deadbands and limits, pushes updates to browsers over a WebSocket and hands
batches to a writer that flushes to the database once a second.

## Docker

```bash
docker build -t tagscope .
docker run -p 8000:8000 tagscope demo --host 0.0.0.0 --no-browser
docker run -p 8000:8000 -v $PWD:/data tagscope          # uses ./tagscope.yaml
```

## Development

```bash
git clone https://github.com/DanShinde/tagscope && cd tagscope
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # includes end-to-end tests against the simulated OPC UA server
ruff check . && ruff format --check .
```

The dashboard is plain HTML, CSS and JavaScript in `src/tagscope/static`, no build step.
Charts use [uPlot](https://github.com/leeoniya/uPlot) (MIT), bundled for offline use.

## Roadmap

- Write values back to the PLC from the dashboard (opt-in, with confirmation)
- Add tags from the address space browser without editing YAML
- Modbus TCP and Siemens S7 (snap7) sources
- MQTT / Sparkplug B output
- Desktop build (single executable for Windows engineering laptops)

Ideas and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the [good first issues](https://github.com/DanShinde/tagscope/labels/good%20first%20issue).

## License

[MIT](LICENSE) © Dan Shinde
