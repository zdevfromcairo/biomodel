"""Filesystem-based ingestion: watch a directory and emit batch events.

Polling-based by design so we work without inotify and stay cross-platform.
A file is considered "ready" once its size is stable across two polls.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SUFFIXES = {".csv", ".jsonl", ".parquet"}


@dataclass
class BatchEvent:
    path: Path
    detected_at: float
    size_bytes: int


@dataclass
class _Pending:
    size: int
    last_seen: float


@dataclass
class FilesystemQueue:
    """Tiny durable queue: state lives in a JSON sidecar.

    Entries are added with :meth:`enqueue` and removed by :meth:`acknowledge`
    after the consumer has successfully processed them. ``in_flight`` items can
    be re-claimed after ``visibility_timeout`` seconds for at-least-once delivery.
    """

    path: Path
    visibility_timeout: float = 300.0

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"pending": [], "in_flight": {}}
        return json.loads(self.path.read_text() or "{}")

    def _write(self, state: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def enqueue(self, item: str) -> None:
        s = self._read()
        if item not in s["pending"] and item not in s["in_flight"]:
            s["pending"].append(item)
            self._write(s)

    def claim(self) -> str | None:
        s = self._read()
        now = time.time()
        # reclaim expired in-flight items
        for k, ts in list(s["in_flight"].items()):
            if now - float(ts) > self.visibility_timeout:
                s["in_flight"].pop(k)
                if k not in s["pending"]:
                    s["pending"].insert(0, k)
        if not s["pending"]:
            self._write(s)
            return None
        item = s["pending"].pop(0)
        s["in_flight"][item] = now
        self._write(s)
        return item

    def acknowledge(self, item: str) -> None:
        s = self._read()
        s["in_flight"].pop(item, None)
        if item in s["pending"]:
            s["pending"].remove(item)
        self._write(s)

    def fail(self, item: str) -> None:
        """Return an in-flight item to the back of the pending queue."""
        s = self._read()
        s["in_flight"].pop(item, None)
        if item not in s["pending"]:
            s["pending"].append(item)
        self._write(s)

    def stats(self) -> dict:
        s = self._read()
        return {"pending": len(s["pending"]), "in_flight": len(s["in_flight"])}


@dataclass
class DirectoryWatcher:
    """Polling directory watcher that emits :class:`BatchEvent` for stable files."""

    directory: Path
    suffixes: Iterable[str] = field(default_factory=lambda: SUPPORTED_SUFFIXES)
    on_event: Callable[[BatchEvent], None] | None = None
    poll_interval: float = 2.0
    stable_polls: int = 2

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, _Pending] = {}
        self._emitted: set[str] = set()
        self.suffixes = {s.lower() for s in self.suffixes}

    def scan_once(self) -> list[BatchEvent]:
        """Single non-blocking pass. Returns events that became stable this call."""
        ready: list[BatchEvent] = []
        now = time.time()
        for p in sorted(self.directory.iterdir()):
            if not p.is_file() or p.suffix.lower() not in self.suffixes:
                continue
            key = str(p.resolve())
            if key in self._emitted:
                continue
            try:
                size = p.stat().st_size
            except FileNotFoundError:
                continue
            prev = self._pending.get(key)
            if prev is None:
                self._pending[key] = _Pending(size=size, last_seen=now)
                continue
            if prev.size == size:
                # stable for one extra poll → emit
                self._pending.pop(key, None)
                self._emitted.add(key)
                ev = BatchEvent(path=p, detected_at=now, size_bytes=size)
                ready.append(ev)
                if self.on_event:
                    self.on_event(ev)
            else:
                prev.size = size
                prev.last_seen = now
        return ready

    def run(self, *, max_iters: int | None = None) -> int:
        """Blocking poll loop. Returns the total number of events emitted.

        ``max_iters`` is mainly used by tests to bound the loop.
        """
        emitted = 0
        i = 0
        while max_iters is None or i < max_iters:
            emitted += len(self.scan_once())
            time.sleep(self.poll_interval)
            i += 1
        return emitted

    def reset(self) -> None:
        self._pending.clear()
        self._emitted.clear()
