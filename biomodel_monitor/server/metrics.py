"""Tiny dependency-free Prometheus exposition.

Implements just enough of the Prometheus exposition format to publish
counters and histograms without pulling in ``prometheus-client``. This keeps
the server's optional dependencies minimal.

Why not ``prometheus_client``?
- One more transitive dep on a hot path.
- We need only counters + histograms with labels; the surface here is ~80 LoC.
- The exposition format is stable and trivially scrape-able by any Prometheus.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field


def _format_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape(str(v))}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@dataclass
class _Counter:
    name: str
    help: str
    values: dict[tuple[tuple[str, str], ...], float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        self.values[key] += float(amount)

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        for key, val in self.values.items():
            yield f"{self.name}{_format_labels(dict(key))} {val}"


@dataclass
class _Histogram:
    name: str
    help: str
    buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    counts: dict[tuple[tuple[str, str], ...], list[int]] = field(default_factory=dict)
    sums: dict[tuple[tuple[str, str], ...], float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        if key not in self.counts:
            self.counts[key] = [0] * (len(self.buckets) + 1)  # +1 = total / +Inf
        for i, b in enumerate(self.buckets):
            if value <= b:
                self.counts[key][i] += 1
        self.counts[key][-1] += 1
        self.sums[key] += float(value)

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        for key, counts in self.counts.items():
            label_dict = dict(key)
            cumulative = 0
            for b, c in zip(self.buckets, counts[:-1]):
                cumulative += c
                ld = {**label_dict, "le": f"{b}"}
                yield f"{self.name}_bucket{_format_labels(ld)} {cumulative}"
            ld = {**label_dict, "le": "+Inf"}
            yield f"{self.name}_bucket{_format_labels(ld)} {counts[-1]}"
            yield f"{self.name}_count{_format_labels(label_dict)} {counts[-1]}"
            yield f"{self.name}_sum{_format_labels(label_dict)} {self.sums[key]}"


class PrometheusRegistry:
    """Thread-safe counters + histograms with text exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, _Counter] = {}
        self._histograms: dict[str, _Histogram] = {}

    def counter(self, name: str, help: str = "") -> _Counter:  # noqa: A002
        with self._lock:
            c = self._counters.get(name)
            if c is None:
                c = _Counter(name=name, help=help or name)
                self._counters[name] = c
            return c

    def histogram(
        self, name: str, help: str = "",  # noqa: A002
        buckets: tuple[float, ...] | None = None,
    ) -> _Histogram:
        with self._lock:
            h = self._histograms.get(name)
            if h is None:
                kw: dict = {"name": name, "help": help or name}
                if buckets is not None:
                    kw["buckets"] = buckets
                h = _Histogram(**kw)
                self._histograms[name] = h
            return h

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for c in self._counters.values():
                lines.extend(c.render())
            for h in self._histograms.values():
                lines.extend(h.render())
        return "\n".join(lines) + "\n"


class TimerContext:
    """Context manager that records elapsed seconds into a histogram."""

    def __init__(self, hist: _Histogram, labels: dict[str, str] | None = None) -> None:
        self._hist = hist
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> TimerContext:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._hist.observe(time.perf_counter() - self._start, self._labels)


__all__ = ["PrometheusRegistry", "TimerContext"]
