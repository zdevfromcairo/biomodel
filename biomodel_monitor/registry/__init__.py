"""Model registry with content-addressed lineage (v0.9).

The registry is a small SQLite-backed catalogue of model versions and their
lineage edges. It deliberately does *not* try to replace MLflow / Weights &
Biases — it answers the operations questions Monitor users keep asking:

* "Which exact dataset hash trained this model version?"
* "Which downstream models depend on it?"
* "Has anyone *quarantined* it?"

Quarantine ties into v0.9 governance: a quarantined model rejects new runs
unless explicitly overridden, and the state change is auditable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ModelStatus = Literal["active", "quarantined", "retired"]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class ModelRecord:
    model_id: str
    model_version: str
    training_data_hash: str | None = None
    framework: str | None = None
    created_at: str = field(default_factory=_now)
    status: ModelStatus = "active"
    notes: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LineageEdge:
    upstream_model_id: str
    upstream_model_version: str
    downstream_model_id: str
    downstream_model_version: str
    kind: str = "derives_from"
    created_at: str = field(default_factory=_now)

    def as_dict(self) -> dict:
        return asdict(self)


class QuarantineError(RuntimeError):
    """Raised when an operation is rejected because the model is quarantined."""


class ModelRegistry:
    """Content-addressed catalogue of model versions + lineage edges."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn.cursor()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS models (
                model_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                training_data_hash TEXT,
                framework TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT,
                PRIMARY KEY (model_id, model_version)
            );
            CREATE TABLE IF NOT EXISTS lineage (
                upstream_model_id TEXT NOT NULL,
                upstream_model_version TEXT NOT NULL,
                downstream_model_id TEXT NOT NULL,
                downstream_model_version TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'derives_from',
                created_at TEXT NOT NULL,
                PRIMARY KEY (upstream_model_id, upstream_model_version,
                             downstream_model_id, downstream_model_version)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- models
    def register(self, record: ModelRecord) -> ModelRecord:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO models (
                model_id, model_version, training_data_hash, framework,
                created_at, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.model_id, record.model_version, record.training_data_hash,
                record.framework, record.created_at, record.status, record.notes,
            ),
        )
        self._conn.commit()
        return record

    def get(self, model_id: str, model_version: str) -> ModelRecord | None:
        row = self._conn.execute(
            "SELECT * FROM models WHERE model_id=? AND model_version=?",
            (model_id, model_version),
        ).fetchone()
        return None if row is None else ModelRecord(**dict(row))

    def list(self, *, model_id: str | None = None,
             status: ModelStatus | None = None) -> list[ModelRecord]:
        sql = "SELECT * FROM models WHERE 1=1"
        params: list = []
        if model_id:
            sql += " AND model_id = ?"
            params.append(model_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [ModelRecord(**dict(r)) for r in rows]

    # --------------------------------------------------------------- status
    def quarantine(self, model_id: str, model_version: str,
                   *, note: str | None = None) -> ModelRecord:
        rec = self.get(model_id, model_version)
        if rec is None:
            raise KeyError(f"unknown model {model_id} v{model_version}")
        rec.status = "quarantined"
        if note:
            rec.notes = note
        self.register(rec)
        return rec

    def unquarantine(self, model_id: str, model_version: str) -> ModelRecord:
        rec = self.get(model_id, model_version)
        if rec is None:
            raise KeyError(f"unknown model {model_id} v{model_version}")
        rec.status = "active"
        self.register(rec)
        return rec

    def assert_active(self, model_id: str, model_version: str) -> None:
        """Raise :class:`QuarantineError` if the model is quarantined.

        If the model isn't registered at all, the call is a no-op so legacy
        deployments without the registry keep working.
        """
        rec = self.get(model_id, model_version)
        if rec is not None and rec.status == "quarantined":
            raise QuarantineError(
                f"model {model_id} v{model_version} is quarantined"
            )

    # -------------------------------------------------------------- lineage
    def add_edge(self, edge: LineageEdge) -> LineageEdge:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO lineage (
                upstream_model_id, upstream_model_version,
                downstream_model_id, downstream_model_version,
                kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                edge.upstream_model_id, edge.upstream_model_version,
                edge.downstream_model_id, edge.downstream_model_version,
                edge.kind, edge.created_at,
            ),
        )
        self._conn.commit()
        return edge

    def upstreams(self, model_id: str, model_version: str) -> list[LineageEdge]:
        rows = self._conn.execute(
            """
            SELECT * FROM lineage
            WHERE downstream_model_id = ? AND downstream_model_version = ?
            """,
            (model_id, model_version),
        ).fetchall()
        return [LineageEdge(**dict(r)) for r in rows]

    def downstreams(self, model_id: str, model_version: str) -> list[LineageEdge]:
        rows = self._conn.execute(
            """
            SELECT * FROM lineage
            WHERE upstream_model_id = ? AND upstream_model_version = ?
            """,
            (model_id, model_version),
        ).fetchall()
        return [LineageEdge(**dict(r)) for r in rows]


__all__ = [
    "LineageEdge",
    "ModelRecord",
    "ModelRegistry",
    "ModelStatus",
    "QuarantineError",
]
