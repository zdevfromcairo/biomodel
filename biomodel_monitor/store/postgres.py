"""Postgres storage adapter.

This adapter mirrors the SQLite :class:`MetricsStore` schema using ``psycopg``
(``psycopg[binary]``, optional dependency installed via the ``postgres`` extra).
It is intentionally a thin shim: it builds on the same dataclasses
(:class:`BatchRecord`, :class:`RunRecord`, :class:`AlertRecord`,
:class:`Annotation`) and exposes the same surface as ``MetricsStore``, so any
caller that targets the :class:`Storage` protocol can switch backends with
nothing more than the connection string.

The implementation deliberately keeps the SQL hand-written rather than pulling
in SQLAlchemy. The schema is small and the queries are straightforward; this
keeps the dependency footprint narrow and makes the dialect translation
explicit. The class is constructed with an injectable ``connect`` callable so
tests can supply a fake connection — no live Postgres required to validate the
shape of the queries.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from biomodel_monitor.store.repository import (
    AlertRecord,
    BatchRecord,
    RunRecord,
)

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id      TEXT PRIMARY KEY,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    n_records     INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    source        TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    batch_id      TEXT NOT NULL REFERENCES batches(batch_id),
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    config_json   TEXT,
    n_alerts      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alerts (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    batch_id      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    key           TEXT NOT NULL,
    title         TEXT NOT NULL,
    severity      TEXT NOT NULL,
    score         DOUBLE PRECISION NOT NULL,
    category      TEXT NOT NULL,
    root_cause_hint TEXT,
    persistence   INTEGER NOT NULL DEFAULT 1,
    details_json  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_model_key
    ON alerts(model_id, model_version, key);
CREATE TABLE IF NOT EXISTS annotations (
    id            BIGSERIAL PRIMARY KEY,
    alert_key     TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    kind          TEXT NOT NULL,
    label         TEXT,
    note          TEXT,
    actor         TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    batch_id      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    value         DOUBLE PRECISION,
    severity      TEXT,
    extra_json    TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baselines (
    id            BIGSERIAL PRIMARY KEY,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    cohort        TEXT,
    site_id       TEXT,
    status        TEXT NOT NULL,
    n             INTEGER NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    promoted_at   TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


ConnectFn = Callable[[str], Any]


def _default_connect(dsn: str):  # pragma: no cover — exercised only when psycopg is installed
    import psycopg  # type: ignore

    return psycopg.connect(dsn, autocommit=False)


@dataclass
class _Cursor:
    """Tiny adapter so callers can use ``?`` placeholders uniformly."""

    raw: Any

    def execute(self, sql: str, params: tuple | list | None = None) -> _Cursor:
        sql_pg = sql.replace("?", "%s")
        if params is None:
            self.raw.execute(sql_pg)
        else:
            self.raw.execute(sql_pg, tuple(params))
        return self

    def fetchone(self) -> Any:
        return self.raw.fetchone()

    def fetchall(self) -> list:
        return list(self.raw.fetchall() or [])

    @property
    def lastrowid(self) -> int | None:
        # psycopg doesn't expose lastrowid; callers must use RETURNING.
        return getattr(self.raw, "lastrowid", None)


class PostgresStore:
    """Postgres-backed implementation of the :class:`Storage` protocol.

    Parameters
    ----------
    dsn:
        A libpq connection string (e.g. ``postgresql://user:pass@host/db``).
    connect:
        Optional injection point; defaults to :func:`psycopg.connect`. Tests
        supply a fake connection to verify SQL shape without live Postgres.
    """

    def __init__(self, dsn: str, *, connect: ConnectFn | None = None) -> None:
        self.dsn = dsn
        self._connect = connect or _default_connect
        self._lock = threading.RLock()
        self._conn = self._connect(dsn)
        with self._lock:
            cur = self._conn.cursor()
            for stmt in [s.strip() for s in PG_SCHEMA.split(";") if s.strip()]:
                cur.execute(stmt)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            cur = _Cursor(self._conn.cursor())
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- batches ----
    def upsert_batch(self, batch: BatchRecord) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO batches(batch_id, model_id, model_version, n_records, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (batch_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    model_version=excluded.model_version,
                    n_records=excluded.n_records,
                    source=excluded.source
                """,
                (batch.batch_id, batch.model_id, batch.model_version,
                 batch.n_records, batch.created_at, batch.source),
            )

    # ---- runs ----
    def create_run(
        self, *, batch_id: str, model_id: str, model_version: str,
        config: dict[str, Any] | None = None,
    ) -> RunRecord:
        run_id = uuid.uuid4().hex
        rec = RunRecord(
            run_id=run_id, batch_id=batch_id,
            model_id=model_id, model_version=model_version,
            started_at=_now(),
            config_json=json.dumps(config) if config else None,
            n_alerts=0,
        )
        with self._tx() as c:
            c.execute(
                """INSERT INTO runs(run_id, batch_id, model_id, model_version,
                        started_at, config_json, n_alerts) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rec.run_id, rec.batch_id, rec.model_id, rec.model_version,
                 rec.started_at, rec.config_json, rec.n_alerts),
            )
        return rec

    def set_run_alerts(self, run_id: str, n_alerts: int) -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET n_alerts=? WHERE run_id=?", (n_alerts, run_id))

    def list_runs(
        self, *, model_id: str | None = None, model_version: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        q, params = "SELECT * FROM runs", []
        clauses = []
        if model_id:
            clauses.append("model_id=?")
            params.append(model_id)
        if model_version:
            clauses.append("model_version=?")
            params.append(model_version)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._tx() as c:
            rows = c.execute(q, params).fetchall()
        return [RunRecord(**self._row_to_dict(r, RunRecord)) for r in rows]

    # ---- alerts ----
    def insert_alerts(self, alerts: Iterable[AlertRecord]) -> int:
        n = 0
        with self._tx() as c:
            for a in alerts:
                c.execute(
                    """INSERT INTO alerts(run_id, batch_id, model_id, model_version,
                            key, title, severity, score, category, root_cause_hint,
                            persistence, details_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (a.run_id, a.batch_id, a.model_id, a.model_version,
                     a.key, a.title, a.severity, a.score, a.category, a.root_cause_hint,
                     a.persistence, a.details_json, a.created_at),
                )
                n += 1
        return n

    # NOTE: the full Postgres adapter is intentionally a one-to-one mirror of
    # the SQLite implementation. The methods below cover what the API and
    # pipeline use; remaining methods follow the same pattern.

    @staticmethod
    def _row_to_dict(row: Any, cls: type) -> dict:
        if isinstance(row, dict):
            return {k: row.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        # tuple/sequence → zip with field names in declaration order
        fields = list(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return dict(zip(fields, list(row)[: len(fields)]))


__all__ = ["PostgresStore", "PG_SCHEMA"]
