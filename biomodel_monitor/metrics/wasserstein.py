"""Sliced 1-D Wasserstein drift (v0.9).

Two-sample 1-D Wasserstein-1 distance has a closed form via the integral of
the absolute difference between the empirical CDFs (or equivalently the
absolute difference of order statistics on a common quantile grid). For
multivariate inputs we use **sliced Wasserstein**: project both samples onto
``n_projections`` random unit directions, compute 1-D W₁ on each, and
average. This is fast, deterministic given a seed, and complements PSI/MMD
because W₁ is sensitive to *how far* the distribution moved, not just how
*different* its shape is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class WassersteinResult:
    name: str = "sliced_wasserstein"
    value: float = 0.0
    severity: str = "ok"
    n_reference: int = 0
    n_current: int = 0
    n_projections: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "n_reference": self.n_reference,
            "n_current": self.n_current,
            "n_projections": self.n_projections,
            "extra": self.extra,
        }


def _as_array(x: Sequence | np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if a.ndim != 2:
        raise ValueError(f"expected 1D or 2D input, got shape {a.shape}")
    return a


def _w1_1d(a: np.ndarray, b: np.ndarray) -> float:
    """1-D Wasserstein-1 distance between two empirical samples."""
    if a.size == 0 or b.size == 0:
        return 0.0
    n = max(len(a), len(b)) * 2
    qs = (np.arange(n) + 0.5) / n
    qa = np.quantile(a, qs)
    qb = np.quantile(b, qs)
    return float(np.mean(np.abs(qa - qb)))


def sliced_wasserstein(
    reference: Sequence | np.ndarray,
    current: Sequence | np.ndarray,
    *,
    n_projections: int = 64,
    warn: float = 0.10,
    alert: float = 0.25,
    seed: int = 0,
) -> WassersteinResult:
    """Sliced 1-D Wasserstein-1 between two samples.

    For 1-D inputs ``n_projections`` is ignored and the exact 1-D W₁ is
    returned. For 2-D inputs (``(n_samples, n_features)``), the result is
    the mean of W₁ along ``n_projections`` random unit directions.
    """
    ref = _as_array(reference)
    cur = _as_array(current)
    if ref.shape[1] != cur.shape[1]:
        raise ValueError("reference and current must have the same dimensionality")
    m, n = ref.shape[0], cur.shape[0]
    if m == 0 or n == 0:
        return WassersteinResult(
            n_reference=m, n_current=n, severity="ok",
            extra={"note": "empty side"},
        )
    d = ref.shape[1]
    rng = np.random.default_rng(seed)
    if d == 1:
        value = _w1_1d(ref[:, 0], cur[:, 0])
        n_proj = 1
    else:
        n_proj = max(1, int(n_projections))
        directions = rng.normal(size=(n_proj, d))
        # Unit-normalise; guard against the (probability-zero) all-zero draw.
        norms = np.linalg.norm(directions, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        directions = directions / norms
        proj_ref = ref @ directions.T  # (m, n_proj)
        proj_cur = cur @ directions.T  # (n, n_proj)
        value = float(np.mean([
            _w1_1d(proj_ref[:, i], proj_cur[:, i]) for i in range(n_proj)
        ]))
    if value >= alert:
        sev = "alert"
    elif value >= warn:
        sev = "warn"
    else:
        sev = "ok"
    return WassersteinResult(
        value=float(value),
        severity=sev,
        n_reference=m,
        n_current=n,
        n_projections=n_proj,
        extra={"warn": warn, "alert": alert, "dimensions": d},
    )


__all__ = ["WassersteinResult", "sliced_wasserstein"]
