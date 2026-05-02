"""Online CUSUM change detector (v0.8).

Page's two-sided CUSUM detects shifts in the mean of a stream relative to
a known target. Unlike fixed-window forecasting, CUSUM signals as soon as
cumulative evidence crosses a control limit ``h`` (typically 4–5σ) — useful
for catching small persistent shifts that a one-shot test misses.

Two ways to use it:

* :func:`cusum_offline` — given a full series, report the first detection
  index (or ``None``), the maximum statistic reached, and a severity.
* :class:`CUSUMMonitor` — stateful online detector for streaming use.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CUSUMResult:
    name: str = "cusum"
    value: float = 0.0  # max(|S+|, |S-|) reached
    severity: str = "ok"
    direction: str | None = None  # "up", "down", or None
    detected_at: int | None = None  # index of first crossing
    target: float = 0.0
    sigma: float = 1.0
    threshold: float = 4.0
    slack_k: float = 0.5
    n: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "direction": self.direction,
            "detected_at": self.detected_at,
            "target": self.target,
            "sigma": self.sigma,
            "threshold": self.threshold,
            "slack_k": self.slack_k,
            "n": self.n,
            "extra": self.extra,
        }


def _normalise(x: float, target: float, sigma: float) -> float:
    if sigma <= 0:
        return 0.0
    return (x - target) / sigma


class CUSUMMonitor:
    """Two-sided CUSUM with reference value ``target`` and slack ``slack_k``.

    The standard formulation:

        S+_t = max(0, S+_{t-1} + (z_t - k))
        S-_t = max(0, S-_{t-1} - (z_t + k))

    where ``z_t = (x_t - target) / sigma`` is the standardised observation,
    ``k`` is the half-shift you want to detect (in standard deviations) and
    a detection fires when either statistic exceeds ``threshold`` ``h``.

    Sensible defaults: ``slack_k=0.5`` (catch a 1σ shift),
    ``threshold=4.0`` (≈ false-alarm rate 1/200 under iid Gaussian).
    """

    def __init__(
        self,
        *,
        target: float,
        sigma: float,
        threshold: float = 4.0,
        slack_k: float = 0.5,
        name: str = "cusum",
    ) -> None:
        if sigma <= 0:
            raise ValueError("sigma must be > 0")
        if threshold <= 0:
            raise ValueError("threshold must be > 0")
        if slack_k < 0:
            raise ValueError("slack_k must be >= 0")
        self.target = float(target)
        self.sigma = float(sigma)
        self.threshold = float(threshold)
        self.slack_k = float(slack_k)
        self.name = name
        self._sp = 0.0
        self._sm = 0.0
        self._n = 0
        self._max = 0.0
        self._direction: str | None = None
        self._detected_at: int | None = None

    @property
    def s_plus(self) -> float:
        return self._sp

    @property
    def s_minus(self) -> float:
        return self._sm

    @property
    def n_seen(self) -> int:
        return self._n

    def update(self, x: float) -> CUSUMResult:
        z = _normalise(float(x), self.target, self.sigma)
        self._sp = max(0.0, self._sp + (z - self.slack_k))
        self._sm = max(0.0, self._sm - (z + self.slack_k))
        self._n += 1
        cur = max(self._sp, self._sm)
        if cur > self._max:
            self._max = cur
        if self._detected_at is None:
            if self._sp > self.threshold:
                self._detected_at = self._n - 1
                self._direction = "up"
            elif self._sm > self.threshold:
                self._detected_at = self._n - 1
                self._direction = "down"
        return self.snapshot()

    def reset(self) -> None:
        """Reset the statistics after operator acknowledgement."""
        self._sp = 0.0
        self._sm = 0.0
        self._direction = None
        self._detected_at = None
        self._max = 0.0
        # Keep ``self._n`` so the timeline isn't lost in /events streams.

    def snapshot(self) -> CUSUMResult:
        warn = 0.5 * self.threshold
        if self._max >= self.threshold:
            sev = "alert"
        elif self._max >= warn:
            sev = "warn"
        else:
            sev = "ok"
        return CUSUMResult(
            name=self.name,
            value=float(self._max),
            severity=sev,
            direction=self._direction,
            detected_at=self._detected_at,
            target=self.target,
            sigma=self.sigma,
            threshold=self.threshold,
            slack_k=self.slack_k,
            n=self._n,
            extra={"s_plus": self._sp, "s_minus": self._sm},
        )


def cusum_offline(
    series: Sequence[float] | Iterable[float],
    *,
    target: float | None = None,
    sigma: float | None = None,
    threshold: float = 4.0,
    slack_k: float = 0.5,
    name: str = "cusum",
) -> CUSUMResult:
    """Convenience: run a CUSUM over a finished series.

    If ``target`` or ``sigma`` is omitted, they are estimated from the first
    third of the series (a simple "reference window" heuristic). This makes
    the function safe to call on a fresh metric stream where the operator
    hasn't yet committed a baseline.
    """
    arr = np.asarray(list(series), dtype=float)
    if arr.size == 0:
        return CUSUMResult(name=name, n=0, severity="ok",
                           extra={"note": "empty series"})
    if target is None or sigma is None:
        head = arr[: max(3, len(arr) // 3)]
        if target is None:
            target = float(np.mean(head))
        if sigma is None:
            sd = float(np.std(head, ddof=1)) if head.size > 1 else 0.0
            # Fall back to a tiny floor so a zero-variance reference doesn't
            # divide-by-zero; the standardisation will still be meaningful
            # because subsequent shifts dominate the floor.
            sigma = sd if sd > 1e-9 else 1.0
    monitor = CUSUMMonitor(
        target=float(target), sigma=float(sigma), threshold=float(threshold),
        slack_k=float(slack_k), name=name,
    )
    last = monitor.snapshot()
    for x in arr:
        last = monitor.update(float(x))
    return last


__all__ = ["CUSUMMonitor", "CUSUMResult", "cusum_offline"]
