"""Thread-safe per-key micro-batching window.

The buffer accepts records keyed by ``(model_id, model_version)`` and emits
:class:`PredictionBatch` instances on two triggers:

* **Size** — the window for a key has reached ``max_records``.
* **Age** — the *oldest* record in a key's window is older than
  ``max_age_s`` seconds (call :meth:`flush_due` periodically; or pass
  ``now`` to drive the clock from a test harness).

The implementation is intentionally minimal:

* No background threads; the caller decides when to call ``flush_due``.
* A single :class:`threading.Lock` for thread safety.
* Pure-stdlib; no external queue dependency.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple

from biomodel_monitor.schema.models import (
    BatchMetadata,
    PredictionBatch,
    PredictionRecord,
)


class WindowKey(NamedTuple):
    """Routing key for a stream — model + version."""

    model_id: str
    model_version: str


@dataclass
class BufferedRecord:
    """A record awaiting inclusion in a flushed batch."""

    record: PredictionRecord
    received_at: float = field(default_factory=time.time)


@dataclass
class _Window:
    records: list[BufferedRecord] = field(default_factory=list)
    opened_at: float | None = None  # time first record entered

    def is_empty(self) -> bool:
        return not self.records

    def age_s(self, now: float) -> float:
        return 0.0 if self.opened_at is None else now - self.opened_at


class WindowBuffer:
    """Per-key micro-batching window.

    Parameters
    ----------
    max_records:
        Trigger a flush as soon as a key's window has this many records.
        Must be >= 1.
    max_age_s:
        Maximum wall-clock age (seconds) of a key's *oldest* record before a
        flush is forced. Must be > 0.
    source:
        Optional source identifier stamped on flushed batches' metadata.
    """

    def __init__(
        self,
        *,
        max_records: int = 256,
        max_age_s: float = 30.0,
        source: str | None = "stream",
    ) -> None:
        if max_records < 1:
            raise ValueError("max_records must be >= 1")
        if max_age_s <= 0:
            raise ValueError("max_age_s must be > 0")
        self.max_records = int(max_records)
        self.max_age_s = float(max_age_s)
        self.source = source
        self._lock = threading.Lock()
        self._windows: dict[WindowKey, _Window] = {}

    # ------------------------------------------------------------------ stats
    def size(self, key: WindowKey | None = None) -> int:
        """Total buffered records (across all keys, or for one key)."""
        with self._lock:
            if key is None:
                return sum(len(w.records) for w in self._windows.values())
            w = self._windows.get(key)
            return 0 if w is None else len(w.records)

    def keys(self) -> list[WindowKey]:
        with self._lock:
            return [k for k, w in self._windows.items() if not w.is_empty()]

    # ----------------------------------------------------------------- add()
    def add(
        self,
        model_id: str,
        model_version: str,
        record: PredictionRecord,
        *,
        now: float | None = None,
    ) -> PredictionBatch | None:
        """Append a record. Return a flushed batch if size threshold was crossed."""
        if not isinstance(record, PredictionRecord):
            raise TypeError("record must be a PredictionRecord")
        ts = time.time() if now is None else float(now)
        key = WindowKey(model_id, model_version)
        with self._lock:
            w = self._windows.setdefault(key, _Window())
            if w.is_empty():
                w.opened_at = ts
            w.records.append(BufferedRecord(record=record, received_at=ts))
            if len(w.records) >= self.max_records:
                return self._drain_locked(key, reason="size", now=ts)
        return None

    # ----------------------------------------------------------- flush_due()
    def flush_due(self, *, now: float | None = None) -> list[PredictionBatch]:
        """Flush any keys whose oldest record exceeds ``max_age_s``."""
        ts = time.time() if now is None else float(now)
        out: list[PredictionBatch] = []
        with self._lock:
            for key in list(self._windows.keys()):
                w = self._windows[key]
                if w.is_empty():
                    continue
                if w.age_s(ts) >= self.max_age_s:
                    out.append(self._drain_locked(key, reason="age", now=ts))
        return out

    def flush_all(self, *, now: float | None = None) -> list[PredictionBatch]:
        """Flush every non-empty key. Useful at shutdown."""
        ts = time.time() if now is None else float(now)
        out: list[PredictionBatch] = []
        with self._lock:
            for key in list(self._windows.keys()):
                if not self._windows[key].is_empty():
                    out.append(self._drain_locked(key, reason="manual", now=ts))
        return out

    # ----------------------------------------------------------- internal
    def _drain_locked(
        self, key: WindowKey, *, reason: str, now: float,
    ) -> PredictionBatch:
        """Caller must hold ``self._lock``."""
        w = self._windows[key]
        records = [br.record for br in w.records]
        opened = w.opened_at or now
        # Reset window in place so concurrent .add() sees an empty window.
        self._windows[key] = _Window()
        meta = BatchMetadata(
            batch_id=f"stream-{key.model_id}-{int(opened * 1000)}-{uuid.uuid4().hex[:6]}",
            model_id=key.model_id,
            model_version=key.model_version,
            created_at=datetime.fromtimestamp(now, tz=timezone.utc),
            source=self.source,
            notes=f"micro-batch flushed by {reason} (n={len(records)})",
        )
        return PredictionBatch(metadata=meta, records=records)


def coerce_records(items: Iterable[dict]) -> list[PredictionRecord]:
    """Convenience: validate a list of dicts into :class:`PredictionRecord`s."""
    return [PredictionRecord.model_validate(it) for it in items]
