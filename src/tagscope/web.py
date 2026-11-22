"""HTTP API, WebSocket feed and the dashboard."""

from __future__ import annotations

import asyncio
import time
from importlib import resources

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .collector import browse
from .hub import Hub
from .storage import Storage


def create_app(hub: Hub, storage: Storage) -> FastAPI:
    app = FastAPI(title="TagScope", version=__version__, docs_url="/api/docs", redoc_url=None)
    static_dir = resources.files("tagscope") / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/api/overview")
    async def overview() -> dict:
        """Tag metadata, latest values, server status and statistics."""
        tags = [
            {
                "name": t.name,
                "server": t.server,
                "node": t.node,
                "unit": t.unit,
                "description": t.description,
                "low": t.low,
                "high": t.high,
            }
            for t in hub.tags.values()
        ]
        return {
            "version": __version__,
            "storage": storage.engine.url.render_as_string(hide_password=True),
            "tags": tags,
            "values": hub.values,
            "servers": list(hub.servers.values()),
            "stats": {**hub.stats, "uptime_s": time.time() - hub.stats["started"]},
        }

    @app.get("/api/history")
    async def history(
        tag: str,
        minutes: float = Query(15, gt=0, le=60 * 24 * 365),
        end: float | None = None,
        points: int = Query(600, ge=10, le=5000),
    ) -> dict:
        """Trend data for one tag, downsampled to at most ``points`` buckets."""
        if tag not in hub.tags:
            raise HTTPException(404, f"unknown tag {tag!r}")
        end = end or time.time()
        start = end - minutes * 60
        data = await asyncio.to_thread(storage.history, tag, start, end, points)
        return {"tag": tag, "start": start, "end": end, **data}

    @app.get("/api/events")
    async def events(limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return await asyncio.to_thread(storage.events, limit)

    @app.get("/api/export.csv")
    async def export_csv(tags: str = "", minutes: float = Query(60, gt=0), end: float | None = None):
        """Download raw samples as CSV."""
        end = end or time.time()
        names = [t for t in tags.split(",") if t] or None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(end))
        return StreamingResponse(
            storage.export_csv(names, end - minutes * 60, end),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="tagscope-{stamp}.csv"'},
        )

    @app.get("/api/browse")
    async def browse_server(server: str, node: str | None = None) -> list[dict]:
        """Browse the address space of a configured server."""
        cfg = next((s for s in hub.config.servers if s.name == server), None)
        if cfg is None:
            raise HTTPException(404, f"unknown server {server!r}")
        try:
            return await asyncio.wait_for(browse(cfg.endpoint, node, cfg.username, cfg.password), 10)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"browse failed: {exc}") from exc

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        queue = hub.subscribe()
        try:
            await ws.send_json(hub.snapshot())
            while True:
                message = await queue.get()
                batch = [message]
                while not queue.empty() and len(batch) < 200:
                    batch.append(queue.get_nowait())
                await ws.send_json({"type": "batch", "messages": batch})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(queue)

    return app
