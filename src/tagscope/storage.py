"""Time-series storage on top of SQLAlchemy Core.

Works with any database SQLAlchemy supports. SQLite is the zero-config
default; PostgreSQL / TimescaleDB is recommended for long-running plants.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    cast,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from .config import TagConfig

metadata = MetaData()

tags_table = Table(
    "tagscope_tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False, unique=True),
    Column("server", String(200), nullable=False, default=""),
    Column("node", String(500), nullable=False, default=""),
    Column("unit", String(50), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("low", Float, nullable=True),
    Column("high", Float, nullable=True),
)

samples_table = Table(
    "tagscope_samples",
    metadata,
    Column("tag_id", Integer, ForeignKey("tagscope_tags.id"), nullable=False),
    Column("ts", Float, nullable=False),
    Column("value", Float, nullable=True),
    Column("quality", Integer, nullable=False, default=0),
    Index("ix_tagscope_samples_tag_ts", "tag_id", "ts"),
)

events_table = Table(
    "tagscope_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False, index=True),
    Column("tag", String(200), nullable=False, default=""),
    Column("kind", String(30), nullable=False),
    Column("message", Text, nullable=False, default=""),
)

#: Quality codes stored alongside each sample.
GOOD, UNCERTAIN, BAD = 0, 1, 2


@dataclass(frozen=True)
class Sample:
    tag: str
    ts: float
    value: float | None
    quality: int = GOOD


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


class Storage:
    """Stores tag metadata, samples and events."""

    def __init__(self, url: str = "sqlite:///tagscope.db") -> None:
        self.url = url
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                # one shared connection, otherwise every thread sees its own empty database
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, future=True, **kwargs)
        if url.startswith("sqlite") and ":memory:" not in url and url != "sqlite://":
            event.listen(self.engine, "connect", _sqlite_pragmas)
        metadata.create_all(self.engine)
        self._ids: dict[str, int] = {}

    # -- tags -------------------------------------------------------------
    def sync_tags(self, tags: Iterable[TagConfig]) -> dict[str, int]:
        """Insert or update tag metadata and return a ``name -> id`` map."""
        with self.engine.begin() as conn:
            existing = {row.name: row.id for row in conn.execute(select(tags_table.c.id, tags_table.c.name))}
            for tag in tags:
                values = {
                    "server": tag.server,
                    "node": tag.node,
                    "unit": tag.unit,
                    "description": tag.description,
                    "low": tag.low,
                    "high": tag.high,
                }
                if tag.name in existing:
                    conn.execute(update(tags_table).where(tags_table.c.id == existing[tag.name]).values(**values))
                else:
                    result = conn.execute(insert(tags_table).values(name=tag.name, **values))
                    existing[tag.name] = result.inserted_primary_key[0]
        self._ids = existing
        return dict(existing)

    def tag_id(self, name: str) -> int | None:
        if name not in self._ids:
            with self.engine.connect() as conn:
                row = conn.execute(select(tags_table.c.id).where(tags_table.c.name == name)).first()
            if row is None:
                return None
            self._ids[name] = row.id
        return self._ids[name]

    def list_tags(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(tags_table).order_by(tags_table.c.server, tags_table.c.name))
            return [dict(row._mapping) for row in rows]

    # -- samples ----------------------------------------------------------
    def write_samples(self, samples: Iterable[Sample]) -> int:
        rows = []
        for s in samples:
            tag_id = self.tag_id(s.tag)
            if tag_id is not None:
                rows.append({"tag_id": tag_id, "ts": s.ts, "value": s.value, "quality": s.quality})
        if rows:
            with self.engine.begin() as conn:
                conn.execute(insert(samples_table), rows)
        return len(rows)

    def latest(self) -> dict[str, dict]:
        """Most recent sample for every tag."""
        last = (
            select(samples_table.c.tag_id, func.max(samples_table.c.ts).label("ts"))
            .group_by(samples_table.c.tag_id)
            .subquery()
        )
        q = (
            select(tags_table.c.name, samples_table.c.ts, samples_table.c.value, samples_table.c.quality)
            .join(last, (samples_table.c.tag_id == last.c.tag_id) & (samples_table.c.ts == last.c.ts))
            .join(tags_table, tags_table.c.id == samples_table.c.tag_id)
        )
        with self.engine.connect() as conn:
            return {r.name: {"ts": r.ts, "value": r.value, "quality": r.quality} for r in conn.execute(q)}

    def history(self, tag: str, start: float, end: float, max_points: int = 600) -> dict[str, list]:
        """Return samples for ``tag`` in ``[start, end]``.

        If there are more than ``max_points`` samples, they are aggregated into
        equal time buckets with average, minimum and maximum, so a 24 hour
        trend stays fast in the browser.
        """
        tag_id = self.tag_id(tag)
        empty: dict[str, list] = {"t": [], "avg": [], "min": [], "max": []}
        if tag_id is None or end <= start:
            return empty
        where = (samples_table.c.tag_id == tag_id) & (samples_table.c.ts >= start) & (samples_table.c.ts <= end)
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).where(where)).scalar_one()
            if count <= max_points:
                rows = conn.execute(
                    select(samples_table.c.ts, samples_table.c.value).where(where).order_by(samples_table.c.ts)
                ).all()
                values = [r.value for r in rows]
                return {"t": [r.ts for r in rows], "avg": values, "min": values, "max": values}

            bucket_s = (end - start) / max_points
            bucket = cast((samples_table.c.ts - start) / bucket_s, Integer).label("bucket")
            rows = conn.execute(
                select(
                    bucket,
                    func.avg(samples_table.c.ts).label("mid_ts"),
                    func.avg(samples_table.c.value).label("avg"),
                    func.min(samples_table.c.value).label("min"),
                    func.max(samples_table.c.value).label("max"),
                )
                .where(where)
                .group_by(bucket)
                .order_by(bucket)
            ).all()
        return {
            "t": [r.mid_ts for r in rows],
            "avg": [r.avg for r in rows],
            "min": [r.min for r in rows],
            "max": [r.max for r in rows],
        }

    def export_csv(self, tags: list[str] | None, start: float, end: float) -> Iterator[str]:
        """Yield CSV text (header first) for the given tags and time range."""
        q = (
            select(tags_table.c.name, samples_table.c.ts, samples_table.c.value, samples_table.c.quality)
            .join(tags_table, tags_table.c.id == samples_table.c.tag_id)
            .where((samples_table.c.ts >= start) & (samples_table.c.ts <= end))
            .order_by(samples_table.c.ts, tags_table.c.name)
        )
        if tags:
            q = q.where(tags_table.c.name.in_(tags))
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp_utc", "epoch_s", "tag", "value", "quality"])
        with self.engine.connect() as conn:
            for i, r in enumerate(conn.execution_options(stream_results=True).execute(q)):
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.ts)) + f".{int(r.ts % 1 * 1000):03d}Z"
                writer.writerow([iso, f"{r.ts:.3f}", r.name, r.value, ("good", "uncertain", "bad")[r.quality]])
                if i % 500 == 0:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()

    def purge(self, older_than: float) -> int:
        """Delete samples and events older than the given epoch timestamp."""
        with self.engine.begin() as conn:
            n = conn.execute(delete(samples_table).where(samples_table.c.ts < older_than)).rowcount
            conn.execute(delete(events_table).where(events_table.c.ts < older_than))
        return n or 0

    def sample_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(samples_table)).scalar_one()

    # -- events -----------------------------------------------------------
    def add_event(self, kind: str, message: str, tag: str = "", ts: float | None = None) -> dict:
        row = {"ts": ts if ts is not None else time.time(), "tag": tag, "kind": kind, "message": message}
        with self.engine.begin() as conn:
            conn.execute(insert(events_table).values(**row))
        return row

    def events(self, limit: int = 100) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(events_table).order_by(events_table.c.ts.desc()).limit(limit))
            return [dict(r._mapping) for r in rows]

    def close(self) -> None:
        self.engine.dispose()
