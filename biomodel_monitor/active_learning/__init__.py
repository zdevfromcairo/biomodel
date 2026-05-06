"""Active learning queue for closed-loop monitoring (v0.10).

When monitoring flags drift or miscalibration, the cheapest fix is often the
*right* fix: send the records the model is least sure about to a human
expert, get a label, and feed it back into calibration / silent-failure
recomputation.

This module gives you three things:

* **Query strategies** that score records by *how informative a label would
  be*: ``entropy``, ``margin``, ``least_confidence``, and ``BALD`` (for
  Monte-Carlo dropout outputs).
* A persistent **priority queue** keyed by ``(model_id, model_version,
  record_id)`` so reviewers can pick up where they left off across restarts.
* A small **labelling workflow** API: ``next_batch`` / ``submit_label`` /
  ``stats``.

The store is plain SQLite (``check_same_thread=False`` + lock so it can be
shared across FastAPI worker threads, matching the v0.9 ``ModelRegistry``).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

QueryStrategy = Literal["entropy", "margin", "least_confidence", "bald"]
QueueStatus = Literal["pending", "labelled", "skipped"]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Query strategies
# --------------------------------------------------------------------------- #


def _normalise_probs(probs: Sequence[float]) -> list[float]:
    p = [float(x) for x in probs]
    s = sum(p)
    if s <= 0 or not all(math.isfinite(x) for x in p):
        raise ValueError("probabilities must be finite and sum to a positive number")
    return [x / s for x in p]


def entropy_score(probs: Sequence[float]) -> float:
    """Shannon entropy in nats — higher = more uncertain."""
    p = _normalise_probs(probs)
    return -sum(x * math.log(x) for x in p if x > 0.0)


def margin_score(probs: Sequence[float]) -> float:
    """``1 - (top1 - top2)`` — higher = closer call between top classes."""
    p = sorted(_normalise_probs(probs), reverse=True)
    if len(p) == 1:
        return 0.0
    return 1.0 - (p[0] - p[1])


def least_confidence_score(probs: Sequence[float]) -> float:
    """``1 - max(p)`` — higher = lowest top-1 confidence."""
    p = _normalise_probs(probs)
    return 1.0 - max(p)


def bald_score(mc_probs: Sequence[Sequence[float]]) -> float:
    """**B**ayesian **A**ctive **L**earning by **D**isagreement.

    ``mc_probs`` is a sequence of *T* MC-dropout posterior samples, each a
    probability vector. BALD = entropy of the mean − mean of the entropies.
    """
    if not mc_probs:
        raise ValueError("BALD requires at least one MC sample")
    T = len(mc_probs)
    K = len(mc_probs[0])
    mean = [0.0] * K
    mean_ent = 0.0
    for sample in mc_probs:
        if len(sample) != K:
            raise ValueError("all MC samples must share the same support size")
        norm = _normalise_probs(sample)
        mean_ent += entropy_score(norm)
        for k, v in enumerate(norm):
            mean[k] += v / T
    mean_ent /= T
    ent_of_mean = entropy_score(mean)
    return max(0.0, ent_of_mean - mean_ent)


_STRATEGY_FNS = {
    "entropy": entropy_score,
    "margin": margin_score,
    "least_confidence": least_confidence_score,
}


def score_record(probs: Sequence[float], *,
                 strategy: QueryStrategy = "entropy") -> float:
    """Return a single uncertainty score for *probs* under *strategy*."""
    if strategy == "bald":
        raise ValueError(
            "BALD requires multiple MC samples; call bald_score() directly"
        )
    if strategy not in _STRATEGY_FNS:
        raise ValueError(f"unknown query strategy: {strategy!r}")
    return _STRATEGY_FNS[strategy](probs)


# --------------------------------------------------------------------------- #
# Persistent queue
# --------------------------------------------------------------------------- #


@dataclass
class QueueItem:
    model_id: str
    model_version: str
    record_id: str
    score: float
    strategy: QueryStrategy
    status: QueueStatus = "pending"
    label: int | str | None = None
    note: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class ActiveLearningQueue:
    """Persistent priority queue of records awaiting expert labels."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS al_queue (
                model_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                record_id TEXT NOT NULL,
                score REAL NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                label TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                PRIMARY KEY (model_id, model_version, record_id)
            );
            CREATE INDEX IF NOT EXISTS al_queue_pending_score
                ON al_queue (model_id, model_version, status, score DESC);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ enqueue
    def enqueue(self, item: QueueItem) -> QueueItem:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO al_queue (
                    model_id, model_version, record_id, score, strategy,
                    status, label, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id, model_version, record_id)
                DO UPDATE SET
                    score = excluded.score,
                    strategy = excluded.strategy
                WHERE al_queue.status = 'pending'
                """,
                (
                    item.model_id, item.model_version, item.record_id,
                    float(item.score), item.strategy, item.status,
                    None if item.label is None else str(item.label),
                    item.note, item.created_at, item.updated_at,
                ),
            )
            self._conn.commit()
        return item

    def enqueue_many(self, items: Sequence[QueueItem]) -> int:
        n = 0
        for it in items:
            self.enqueue(it)
            n += 1
        return n

    # ---------------------------------------------------------------- dequeue
    def next_batch(self, model_id: str, model_version: str, *,
                   limit: int = 10) -> list[QueueItem]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM al_queue
                WHERE model_id=? AND model_version=? AND status='pending'
                ORDER BY score DESC, created_at ASC
                LIMIT ?
                """,
                (model_id, model_version, int(limit)),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    # ------------------------------------------------------------- submit
    def submit_label(self, model_id: str, model_version: str, record_id: str,
                     *, label: int | str, note: str | None = None) -> QueueItem:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE al_queue
                SET status='labelled', label=?, note=?, updated_at=?
                WHERE model_id=? AND model_version=? AND record_id=?
                """,
                (str(label), note, _now(), model_id, model_version, record_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise KeyError(
                    f"no queue item for {model_id}/{model_version}/{record_id}"
                )
            row = self._conn.execute(
                """
                SELECT * FROM al_queue
                WHERE model_id=? AND model_version=? AND record_id=?
                """,
                (model_id, model_version, record_id),
            ).fetchone()
        return _row_to_item(row)

    def skip(self, model_id: str, model_version: str, record_id: str,
             *, note: str | None = None) -> QueueItem:
        with self._lock:
            self._conn.execute(
                """
                UPDATE al_queue
                SET status='skipped', note=?, updated_at=?
                WHERE model_id=? AND model_version=? AND record_id=?
                """,
                (note, _now(), model_id, model_version, record_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM al_queue
                WHERE model_id=? AND model_version=? AND record_id=?
                """,
                (model_id, model_version, record_id),
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return _row_to_item(row)

    # ------------------------------------------------------------- stats
    def stats(self, model_id: str, model_version: str) -> dict:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*) AS n, AVG(score) AS avg_score
                FROM al_queue
                WHERE model_id=? AND model_version=?
                GROUP BY status
                """,
                (model_id, model_version),
            ).fetchall()
        out: dict = {"model_id": model_id, "model_version": model_version,
                     "by_status": {}}
        for r in rows:
            out["by_status"][r["status"]] = {
                "n": int(r["n"]),
                "avg_score": float(r["avg_score"] or 0.0),
            }
        return out

    def labels(self, model_id: str, model_version: str) -> list[QueueItem]:
        """Return only the *labelled* items — for feedback into calibration."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM al_queue
                WHERE model_id=? AND model_version=? AND status='labelled'
                ORDER BY updated_at ASC
                """,
                (model_id, model_version),
            ).fetchall()
        return [_row_to_item(r) for r in rows]


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    d = dict(row)
    label = d.get("label")
    if label is not None:
        try:
            label = int(label)
        except ValueError:
            pass
    d["label"] = label
    return QueueItem(**d)


__all__ = [
    "ActiveLearningQueue",
    "QueryStrategy",
    "QueueItem",
    "QueueStatus",
    "bald_score",
    "entropy_score",
    "least_confidence_score",
    "margin_score",
    "score_record",
]
