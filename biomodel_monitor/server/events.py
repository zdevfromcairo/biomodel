"""Live event bus + subscribers for the BioModel Monitor server (v0.8).

A tiny in-process pub/sub used by the FastAPI app to push notable events
to any connected WebSocket. Topology:

    app code  ->  EventBus.publish(Event(...))    (fire-and-forget)
                          |
                          v
                  per-subscriber asyncio.Queue
                          |
                          v
                  /ws/events client (or SDK helper)

Subscribers are :class:`asyncio.Queue` references; the bus never blocks the
publisher (a slow consumer drops events at its own queue). The Event JSON
shape is intentionally stable so external dashboards can consume the stream.

The module only needs the standard library; ``asyncio`` is part of the
stdlib and the FastAPI server already runs an event loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventType = Literal[
    "alert.emitted",
    "run.completed",
    "incident.annotated",
    "baseline.promoted",
    "ingest.flushed",
    "system.info",
]


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant_id: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


class EventBus:
    """In-process pub/sub. Thread-safe for synchronous publishers; async-safe
    for subscribers (each subscriber owns its own queue)."""

    def __init__(self, *, max_queue: int = 1024, history: int = 200) -> None:
        if max_queue <= 0:
            raise ValueError("max_queue must be > 0")
        if history < 0:
            raise ValueError("history must be >= 0")
        self.max_queue = int(max_queue)
        self._history_size = int(history)
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._history: list[Event] = []
        self._dropped = 0

    @property
    def n_subscribers(self) -> int:
        return len(self._subscribers)

    @property
    def dropped(self) -> int:
        return self._dropped

    def history(self, *, types: Iterable[str] | None = None,
                limit: int | None = None) -> list[Event]:
        items = self._history
        if types is not None:
            wanted = set(types)
            items = [e for e in items if e.type in wanted]
        if limit is not None:
            items = items[-int(limit):]
        return list(items)

    def subscribe(self) -> asyncio.Queue[Event]:
        """Return a fresh queue for a new subscriber."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.max_queue)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        """Fan out an event to all live subscribers.

        This is intentionally non-blocking: if a subscriber's queue is full
        we drop the oldest event to make room, so the publisher is never
        delayed by a slow client.
        """
        # Append to history.
        self._history.append(event)
        if len(self._history) > self._history_size:
            del self._history[: len(self._history) - self._history_size]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest, retry once. If still full, give up on this event
                # for this subscriber so the rest aren't penalised.
                self._dropped += 1
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


__all__ = ["Event", "EventBus", "EventType"]
