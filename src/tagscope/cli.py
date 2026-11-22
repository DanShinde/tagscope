"""Command line interface: ``tagscope demo | run | init | browse | export``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config

EXAMPLE_CONFIG = """\
# TagScope configuration. Values like ${PLC_PASSWORD} are read from the environment.
storage: sqlite:///tagscope.db          # or postgresql+psycopg://user:pass@host/db
retention_days: 30                       # delete samples older than this (null = keep forever)

web:
  host: 127.0.0.1
  port: 8000

servers:
  - name: line1
    endpoint: opc.tcp://192.168.0.10:4840
    # username: operator
    # password: ${PLC_PASSWORD}
    publishing_interval_ms: 500
    tags:
      - name: Tank level
        node: ns=3;s="DB_Process"."TankLevel"     # Siemens S7-1500 style node id
        unit: "%"
        low: 10
        high: 90
        deadband: 0.2                    # only store changes larger than this
      - name: Pump running
        node: ns=3;s="DB_Process"."PumpRunning"
      - ns=3;s="DB_Process"."BatchCounter"     # short form: name = node id
"""

BANNER = r"""
  ______            _____
 /_  __/___ _____ _/ ___/_________  ____  ___
  / / / __ `/ __ `/\__ \/ ___/ __ \/ __ \/ _ \
 / / / /_/ / /_/ /___/ / /__/ /_/ / /_/ /  __/
/_/  \__,_/\__, //____/\___/\____/ .___/\___/
          /____/                /_/          v{version}
"""


async def serve(config: Config, *, simulate: bool = False, open_browser: bool = False) -> None:
    """Run the collector, the database writer and the web server together."""
    import uvicorn

    from .collector import watch_server, writer_loop
    from .hub import Hub
    from .simulator import run_simulator
    from .storage import Storage
    from .web import create_app

    tasks: list[asyncio.Task] = []
    storage = Storage(config.storage)
    storage.sync_tags(config.tags)
    if simulate:
        from .simulator import Plant, backfill

        plant = Plant(seed=7)
        await asyncio.to_thread(backfill, storage, config, plant)
        ready = asyncio.Event()
        tasks.append(asyncio.create_task(run_simulator(config.servers[0].endpoint, plant=plant, ready=ready)))
        await asyncio.wait_for(ready.wait(), 15)
    hub = Hub(config)
    tasks.append(asyncio.create_task(writer_loop(hub, storage, config)))
    tasks += [asyncio.create_task(watch_server(s, hub)) for s in config.servers]

    app = create_app(hub, storage)
    uv_config = uvicorn.Config(app, host=config.web.host, port=config.web.port, log_level="warning")
    server = uvicorn.Server(uv_config)
    url = f"http://{'localhost' if config.web.host in ('0.0.0.0', '127.0.0.1') else config.web.host}:{config.web.port}"
    print(BANNER.format(version=__version__))
    print(f"  Dashboard   {url}")
    print(f"  Storage     {storage.engine.url.render_as_string(hide_password=True)}")
    print(f"  Logging     {len(config.tags)} tags from {len(config.servers)} server(s)")
    print("  Press Ctrl+C to stop.\n")
    if open_browser:
        asyncio.get_running_loop().call_later(1.0, webbrowser.open, url)
    try:
        await server.serve()
    finally:
        for task in reversed(tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        storage.close()


def _cmd_demo(args: argparse.Namespace) -> int:
    from .simulator import demo_config

    config = demo_config(storage=args.storage)
    config.web.host, config.web.port = args.host, args.port
    asyncio.run(serve(config, simulate=True, open_browser=not args.no_browser))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.port:
        config.web.port = args.port
    if args.host:
        config.web.host = args.host
    if not config.servers:
        print("error: no servers configured", file=sys.stderr)
        return 2
    asyncio.run(serve(config, open_browser=args.open))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"Wrote {path}. Edit the endpoint and tags, then run: tagscope run {path}")
    return 0


def _cmd_browse(args: argparse.Namespace) -> int:
    from .collector import browse

    items = asyncio.run(browse(args.endpoint, args.node, args.username, args.password))
    width = max((len(i["node"]) for i in items), default=10)
    for item in items:
        icon = {"Object": "▸", "Variable": "•", "Method": "ƒ"}.get(item["class"], " ")
        value = "" if item["value"] is None else f" = {item['value']!r}"
        print(f"{icon} {item['node']:<{width}}  {item['name']}{value}")
    return 0


def _parse_iso8601(value: str) -> float:
    """Parse an ISO 8601 timestamp into epoch seconds; naive values are local time."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"cannot parse {value!r} as an ISO 8601 timestamp (e.g. 2026-11-21T06:00)") from None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def _export_window(args: argparse.Namespace) -> tuple[float, float]:
    """Resolve the export time window: --since/--until, else the last --minutes."""
    end = _parse_iso8601(args.until) if args.until else time.time()
    start = _parse_iso8601(args.since) if args.since else end - args.minutes * 60
    if start > end:
        raise ValueError(f"--since ({args.since}) is after --until ({args.until or 'now'})")
    return start, end


def _cmd_export(args: argparse.Namespace) -> int:
    from .storage import Storage

    config = load_config(args.config)
    try:
        start, end = _export_window(args)
    except ValueError as exc:
        print(f"export error: {exc}", file=sys.stderr)
        return 2
    storage = Storage(config.storage)
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(open(args.output, "w", encoding="utf-8", newline="")) if args.output else sys.stdout
        for chunk in storage.export_csv(args.tags or None, start, end):
            out.write(chunk)
    storage.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tagscope", description="Log OPC UA tags to a database and watch them live in your browser."
    )
    parser.add_argument("--version", action="version", version=f"tagscope {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="start a simulated plant and open the dashboard")
    p.add_argument("--storage", default="sqlite:///tagscope-demo.db")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.set_defaults(func=_cmd_demo)

    p = sub.add_parser("run", help="log the servers and tags from a config file")
    p.add_argument("config", help="path to tagscope.yaml")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("init", help="write an example config file")
    p.add_argument("path", nargs="?", default="tagscope.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("browse", help="list the nodes of an OPC UA server")
    p.add_argument("endpoint", help="e.g. opc.tcp://192.168.0.10:4840")
    p.add_argument("node", nargs="?", help="node id to browse (default: Objects folder)")
    p.add_argument("--username")
    p.add_argument("--password")
    p.set_defaults(func=_cmd_browse)

    p = sub.add_parser("export", help="export logged samples as CSV")
    p.add_argument("config", help="path to tagscope.yaml")
    p.add_argument("--tags", nargs="*", help="tag names (default: all)")
    p.add_argument("--minutes", type=float, default=60,
                   help="export the last N minutes (default: 60, ignored when --since is given)")
    p.add_argument("--since",
                   help="start of the export window as ISO 8601, e.g. 2026-11-21T06:00 (naive = local time)")
    p.add_argument("--until", help="end of the export window as ISO 8601 (default: now); naive = local time")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.set_defaults(func=_cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("asyncua").setLevel(logging.ERROR)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
