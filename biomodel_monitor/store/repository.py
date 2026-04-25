"""SQLite-backed metrics + incident repository.

Schema:

* ``batches``      one row per ingested batch
* ``runs``         one row per pipeline invocation against a batch
* ``alerts``       one row per emitted alert (per run)
* ``annotations``  multi-row audit trail per alert key (ack, comment, resolve, label)
* ``metrics``      flat key/value/severity store for scoring trends across runs
* ``baselines``    persisted (or candidate / promoted) baselines
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
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
    batch_id      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    config_json   TEXT,
    n_alerts      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    batch_id      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    model_version TEXT NOT NULL,
    key           TEXT NOT NULL,
    title         TEXT NOT NULL,
    severity      TEXT NOT NULL,
    score         REAL NOT NULL,
    category      TEXT NOT NULL,
    root_cause_hint TEXT,
    persistence   INTEGER NOT NULL DEFAULT 1,
    details_json  TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(key);
CREATE INDEX IF NOT EXISTS idx_alerts_model ON alerts(model_id, model_version);

CREATE TABLE IF NOT EXISTS annotations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key    TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    model_version TEXT NOT NULL,
    kind         TEXT NOT NULL,
    label        TEXT,
    note         TEXT,
    actor        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_key ON annotations(alert_key);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    model_version TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value        REAL,
    severity     TEXT,
    extra_json   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(model_id, model_version, name);

CREATE TABLE IF NOT EXISTS baselines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
CREATE INDEX IF NOT EXISTS idx_baselines_lookup
    ON baselines(model_id, model_version, cohort, site_id, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BatchRecord:
    batch_id: str
    model_id: str
    model_version: str
    n_records: int
    created_at: str
    source: str | None = None


@dataclass
class RunRecord:
    run_id: str
    batch_id: str
    model_id: str
    model_version: str
    started_at: str
    config_json: str | None = None
    n_alerts: int = 0


@dataclass
class AlertRecord:
    run_id: str
    batch_id: str
    model_id: str
    model_version: str
    key: str
    title: str
    severity: str
    score: float
    category: str
    root_cause_hint: str | None = None
    persistence: int = 1
    details_json: str | None = None
    created_at: str = field(default_factory=_now)
    id: int | None = None


@dataclass
class Annotation:
    alert_key: str
    model_id: str
    model_version: str
    kind: str
    label: str | None = None
    note: str | None = None
    actor: str | None = None
    created_at: str = field(default_factory=_now)
    id: int | None = None


class MetricsStore:
    """Thread-safe SQLite store. Single file, on-prem friendly."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            with self._conn:
                yield self._conn

    # ----- batches -----------------------------------------------------------
    def upsert_batch(self, batch: BatchRecord) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO batches(batch_id, model_id, model_version, n_records, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    model_version=excluded.model_version,
                    n_records=excluded.n_records,
                    source=excluded.source
                """,
                (batch.batch_id, batch.model_id, batch.model_version,
                 batch.n_records, batch.created_at, batch.source),
            )

    # ----- runs --------------------------------------------------------------
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
        q = "SELECT * FROM runs"
        clauses, params = [], []
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
        return [RunRecord(**dict(r)) for r in rows]

    # ----- alerts ------------------------------------------------------------
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

    def alert_persistence(
        self, *, model_id: str, model_version: str, key: str, last_n_runs: int = 5,
    ) -> int:
        """How many of the last N runs (this model+version) emitted ``key``."""
        with self._tx() as c:
            recent_runs = c.execute(
                """SELECT run_id FROM runs
                       WHERE model_id=? AND model_version=?
                       ORDER BY started_at DESC LIMIT ?""",
                (model_id, model_version, last_n_runs),
            ).fetchall()
            if not recent_runs:
                return 0
            run_ids = [r["run_id"] for r in recent_runs]
            ph = ",".join(["?"] * len(run_ids))
            row = c.execute(
                f"""SELECT COUNT(DISTINCT run_id) AS n FROM alerts
                        WHERE key=? AND run_id IN ({ph})""",
                (key, *run_ids),
            ).fetchone()
            return int(row["n"]) if row else 0

    def list_alerts(
        self, *, run_id: str | None = None, model_id: str | None = None,
        model_version: str | None = None, key: str | None = None, limit: int = 200,
    ) -> list[AlertRecord]:
        q = "SELECT * FROM alerts"
        clauses, params = [], []
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if model_id:
            clauses.append("model_id=?")
            params.append(model_id)
        if model_version:
            clauses.append("model_version=?")
            params.append(model_version)
        if key:
            clauses.append("key=?")
            params.append(key)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._tx() as c:
            rows = c.execute(q, params).fetchall()
        return [AlertRecord(**dict(r)) for r in rows]

    # ----- annotations -------------------------------------------------------
    def add_annotation(self, ann: Annotation) -> Annotation:
        with self._tx() as c:
            cur = c.execute(
                """INSERT INTO annotations(alert_key, model_id, model_version, kind,
                        label, note, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ann.alert_key, ann.model_id, ann.model_version, ann.kind,
                 ann.label, ann.note, ann.actor, ann.created_at),
            )
            ann.id = int(cur.lastrowid or 0)
        return ann

    def list_annotations(
        self, *, alert_key: str | None = None, model_id: str | None = None,
        model_version: str | None = None, kind: str | None = None,
    ) -> list[Annotation]:
        q = "SELECT * FROM annotations"
        clauses, params = [], []
        if alert_key:
            clauses.append("alert_key=?")
            params.append(alert_key)
        if model_id:
            clauses.append("model_id=?")
            params.append(model_id)
        if model_version:
            clauses.append("model_version=?")
            params.append(model_version)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at ASC"
        with self._tx() as c:
            rows = c.execute(q, params).fetchall()
        return [Annotation(**dict(r)) for r in rows]

    def alert_status(self, *, model_id: str, model_version: str, key: str) -> str:
        anns = self.list_annotations(
            alert_key=key, model_id=model_id, model_version=model_version
        )
        status = "open"
        for a in anns:
            if a.kind == "ack":
                status = "acknowledged"
            elif a.kind == "resolve":
                return "resolved"
        return status

    def alert_label(
        self, *, model_id: str, model_version: str, key: str
    ) -> str | None:
        anns = [
            a for a in self.list_annotations(
                alert_key=key, model_id=model_id, model_version=model_version
            )
            if a.kind == "label" and a.label
        ]
        return anns[-1].label if anns else None

    # ----- metrics flat-file -------------------------------------------------
    def insert_metric(
        self, *, run_id: str, batch_id: str, model_id: str, model_version: str,
        name: str, kind: str, value: float | None,
        severity: str | None = None, extra: dict | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO metrics(run_id, batch_id, model_id, model_version,
                        name, kind, value, severity, extra_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, batch_id, model_id, model_version, name, kind,
                 None if value is None else float(value),
                 severity, json.dumps(extra) if extra else None, _now()),
            )

    def metric_history(
        self, *, model_id: str, model_version: str, name: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._tx() as c:
            rows = c.execute(
                """SELECT m.*, r.started_at AS run_started_at
                       FROM metrics m JOIN runs r ON m.run_id=r.run_id
                       WHERE m.model_id=? AND m.model_version=? AND m.name=?
                       ORDER BY r.started_at ASC LIMIT ?""",
                (model_id, model_version, name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----- baselines ---------------------------------------------------------
    def save_baseline(
        self, *, model_id: str, model_version: str,
        cohort: str | None, site_id: str | None,
        status: str, payload: dict,
    ) -> int:
        if status not in ("candidate", "promoted", "retired"):
            raise ValueError("status must be candidate|promoted|retired")
        with self._tx() as c:
            cur = c.execute(
                """INSERT INTO baselines(model_id, model_version, cohort, site_id,
                        status, n, payload_json, created_at, promoted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (model_id, model_version, cohort, site_id, status,
                 int(payload.get("n", 0)),
                 json.dumps(payload), _now(),
                 _now() if status == "promoted" else None),
            )
            return int(cur.lastrowid or 0)

    def promote_baseline(self, baseline_id: int) -> None:
        with self._tx() as c:
            row = c.execute("SELECT * FROM baselines WHERE id=?", (baseline_id,)).fetchone()
            if row is None:
                raise KeyError(f"baseline {baseline_id} not found")
            c.execute(
                """UPDATE baselines SET status='retired'
                   WHERE status='promoted' AND model_id=? AND model_version=?
                     AND COALESCE(cohort,'') = COALESCE(?,'')
                     AND COALESCE(site_id,'') = COALESCE(?,'')""",
                (row["model_id"], row["model_version"], row["cohort"], row["site_id"]),
            )
            c.execute(
                "UPDATE baselines SET status='promoted', promoted_at=? WHERE id=?",
                (_now(), baseline_id),
            )

    def get_promoted_baseline(
        self, *, model_id: str, model_version: str,
        cohort: str | None = None, site_id: str | None = None,
    ) -> dict | None:
        with self._tx() as c:
            row = c.execute(
                """SELECT * FROM baselines
                       WHERE model_id=? AND model_version=? AND status='promoted'
                         AND COALESCE(cohort,'') = COALESCE(?,'')
                         AND COALESCE(site_id,'') = COALESCE(?,'')
                       ORDER BY promoted_at DESC LIMIT 1""",
                (model_id, model_version, cohort, site_id),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def list_baselines(
        self, *, model_id: str | None = None, model_version: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        q = "SELECT id, model_id, model_version, cohort, site_id, status, n, created_at, promoted_at FROM baselines"
        clauses, params = [], []
        if model_id:
            clauses.append("model_id=?")
            params.append(model_id)
        if model_version:
            clauses.append("model_version=?")
            params.append(model_version)
        if status:
            clauses.append("status=?")
            params.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC"
        with self._tx() as c:
            rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]
