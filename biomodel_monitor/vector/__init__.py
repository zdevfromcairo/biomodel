"""Embedding store + nearest-neighbor explanations (v0.11).

When the model is uncertain about a record, the most useful explanation we
can give a clinician is often *"this case is similar to these labelled
historical cases"*. This module implements:

* A SQLite-backed **vector store** keyed by ``(namespace, record_id)`` with
  arbitrary metadata. ``namespace`` lets you separate models / cohorts.
* A **brute-force k-NN** query (cosine distance, then L2). Fine for the
  sub-100k vectors most monitoring deployments hold; the
  :class:`VectorIndex` ABC leaves a clean seam for swapping in FAISS / hnsw
  later.
* A simple **explain** convenience that returns the metadata of the
  ``k`` nearest historical neighbours.

The store is thread-safe (``check_same_thread=False`` + lock), matching
the v0.9 :class:`ModelRegistry` and v0.10 :class:`ActiveLearningQueue`.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class Neighbor:
    record_id: str
    distance: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine *distance* in [0, 2]. Returns 1.0 if a vector is zero-length."""
    if len(a) != len(b):
        raise ValueError(
            f"embedding dimension mismatch: {len(a)} vs {len(b)}"
        )
    na = _norm(a)
    nb = _norm(b)
    if na == 0.0 or nb == 0.0:
        return 1.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    sim = dot / (na * nb)
    # numerical guard
    sim = max(-1.0, min(1.0, sim))
    return 1.0 - sim


class VectorStore:
    """SQLite-backed embedding store with brute-force cosine k-NN."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vectors (
                namespace TEXT NOT NULL,
                record_id TEXT NOT NULL,
                dim INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (namespace, record_id)
            );
            CREATE INDEX IF NOT EXISTS vectors_namespace
                ON vectors (namespace);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ----------------------------------------------------------------- writes
    def add(self, namespace: str, record_id: str,
            embedding: Sequence[float], *,
            metadata: dict | None = None) -> None:
        if not embedding:
            raise ValueError("embedding must be non-empty")
        if not all(math.isfinite(float(x)) for x in embedding):
            raise ValueError("embedding values must be finite")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO vectors (
                    namespace, record_id, dim, embedding, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    namespace, record_id, int(len(embedding)),
                    json.dumps([float(x) for x in embedding]),
                    json.dumps(metadata or {}),
                    _now(),
                ),
            )
            self._conn.commit()

    def add_many(self, namespace: str,
                 items: Sequence[tuple[str, Sequence[float], dict | None]]
                 ) -> int:
        n = 0
        for rid, emb, meta in items:
            self.add(namespace, rid, emb, metadata=meta)
            n += 1
        return n

    # ------------------------------------------------------------------ reads
    def count(self, namespace: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM vectors WHERE namespace=?",
                (namespace,),
            ).fetchone()
        return int(row["n"])

    def query(self, namespace: str, embedding: Sequence[float],
              *, k: int = 5) -> list[Neighbor]:
        if k <= 0:
            raise ValueError("k must be positive")
        if not embedding:
            raise ValueError("query embedding must be non-empty")
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_id, dim, embedding, metadata "
                "FROM vectors WHERE namespace=?",
                (namespace,),
            ).fetchall()
        if not rows:
            return []
        q_dim = len(embedding)
        scored: list[Neighbor] = []
        for r in rows:
            if int(r["dim"]) != q_dim:
                # Skip mismatched-dim rows rather than raising — the
                # namespace may have been re-trained at a new embedding size.
                continue
            vec = json.loads(r["embedding"])
            d = _cosine_distance(embedding, vec)
            md_raw = r["metadata"] or "{}"
            try:
                md = json.loads(md_raw)
            except json.JSONDecodeError:
                md = {}
            scored.append(
                Neighbor(record_id=r["record_id"], distance=d, metadata=md)
            )
        scored.sort(key=lambda n: n.distance)
        return scored[:k]

    def explain(self, namespace: str, embedding: Sequence[float],
                *, k: int = 5) -> dict:
        """Return the k-NN result wrapped in a small explanation envelope."""
        neighbours = self.query(namespace, embedding, k=k)
        return {
            "namespace": namespace,
            "k": k,
            "n_total": self.count(namespace),
            "neighbors": [n.as_dict() for n in neighbours],
        }


__all__ = ["Neighbor", "VectorStore"]
