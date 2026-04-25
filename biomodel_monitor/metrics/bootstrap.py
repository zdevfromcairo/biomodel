"""Bootstrap confidence-interval helpers.

Used to put non-parametric CIs around any scalar metric. Deterministic when a
``seed`` is provided so reports are reproducible across re-runs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass
class BootstrapCI:
    point: float
    lo: float
    hi: float
    n_samples: int
    method: str = "percentile"

    def as_dict(self) -> dict:
        return {
            "point": self.point,
            "lo": self.lo,
            "hi": self.hi,
            "n_samples": self.n_samples,
            "method": self.method,
        }


def bootstrap_ci(
    data: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    *,
    n_samples: int = 1000,
    alpha: float = 0.05,
    seed: int | None = 0,
) -> BootstrapCI:
    """Compute a percentile bootstrap CI for ``statistic(data)``."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    arr = np.asarray(data, dtype=float)
    if arr.size == 0:
        raise ValueError("data must be non-empty")
    rng = np.random.default_rng(seed)
    n = arr.size
    samples = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        idx = rng.integers(0, n, size=n)
        samples[i] = float(statistic(arr[idx]))
    lo = float(np.quantile(samples, alpha / 2))
    hi = float(np.quantile(samples, 1 - alpha / 2))
    point = float(statistic(arr))
    return BootstrapCI(point=point, lo=lo, hi=hi, n_samples=n_samples)


def two_sample_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    statistic: Callable[[np.ndarray, np.ndarray], float],
    *,
    n_samples: int = 1000,
    alpha: float = 0.05,
    seed: int | None = 0,
) -> BootstrapCI:
    """Bootstrap CI for ``statistic(a, b)`` over independent resamples."""
    aa = np.asarray(a, dtype=float)
    bb = np.asarray(b, dtype=float)
    if aa.size == 0 or bb.size == 0:
        raise ValueError("both inputs must be non-empty")
    rng = np.random.default_rng(seed)
    out = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        ia = rng.integers(0, aa.size, size=aa.size)
        ib = rng.integers(0, bb.size, size=bb.size)
        out[i] = float(statistic(aa[ia], bb[ib]))
    lo = float(np.quantile(out, alpha / 2))
    hi = float(np.quantile(out, 1 - alpha / 2))
    return BootstrapCI(point=float(statistic(aa, bb)), lo=lo, hi=hi, n_samples=n_samples)
