"""Split-conformal prediction with marginal coverage guarantee (v0.10).

Given a held-out *calibration* set with known labels and any black-box
classifier returning probability vectors, this module produces **prediction
sets** with the property::

    P( y_test ∈ predicted_set ) >= 1 - alpha

over the joint draw of calibration + test data, *under exchangeability*.

This is the simplest, most robust uncertainty quantification method we can
ship without depending on the model's own calibration. It composes with the
v0.4 calibration metrics (a model can be over-confident *and* still produce
sound conformal sets — that's the point).

We implement two scoring functions:

* **APS** (Adaptive Prediction Sets, Romano et al. 2020) — conditional
  coverage friendly; sets are larger but better behaved per-class.
* **LAC** (Least Ambiguous set-valued Classifier, Sadinle et al. 2019) —
  ``score = 1 - p_y``; gives the smallest sets at the cost of conditional
  coverage.

Numpy-only; no scipy dep.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

ScoreFn = str  # "lac" | "aps"


@dataclass
class ConformalCalibration:
    """Calibrated quantile + the score fn it was computed under."""

    score_fn: ScoreFn
    alpha: float
    quantile: float
    n_calibration: int

    def as_dict(self) -> dict:
        return {
            "score_fn": self.score_fn,
            "alpha": self.alpha,
            "quantile": self.quantile,
            "n_calibration": self.n_calibration,
        }


@dataclass
class ConformalResult:
    """Per-input prediction set, severity contract for the v0.4 alert engine."""

    name: str
    value: float           # mean prediction-set size
    severity: str = "ok"   # "ok"|"warn"|"alert" — driven by mean size
    sets: list[list[int]] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "sets": self.sets,
            "extra": self.extra,
        }


def _validate(probs: Sequence[Sequence[float]]) -> np.ndarray:
    p = np.asarray(probs, dtype=float)
    if p.ndim != 2:
        raise ValueError("probabilities must be 2-D (n_samples, n_classes)")
    if p.shape[0] == 0 or p.shape[1] == 0:
        raise ValueError("probabilities must be non-empty")
    if not np.isfinite(p).all():
        raise ValueError("probabilities must be finite")
    if (p < -1e-9).any() or (p > 1 + 1e-9).any():
        raise ValueError("probabilities must lie in [0, 1]")
    rowsum = p.sum(axis=1)
    if not np.allclose(rowsum, 1.0, atol=1e-3):
        # Auto-normalise — it's almost always what the caller wanted.
        p = p / np.clip(rowsum[:, None], 1e-12, None)
    return p


def _aps_scores(probs: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """APS conformity score: cumulative prob from class y down to the largest."""
    order = np.argsort(-probs, axis=1)
    sorted_p = np.take_along_axis(probs, order, axis=1)
    cumsum = np.cumsum(sorted_p, axis=1)
    # rank of the true class within each sorted row
    ranks = np.argmax(order == labels[:, None], axis=1)
    return cumsum[np.arange(probs.shape[0]), ranks]


def _lac_scores(probs: np.ndarray, labels: np.ndarray) -> np.ndarray:
    return 1.0 - probs[np.arange(probs.shape[0]), labels]


def _quantile_at_finite_correction(scores: np.ndarray, alpha: float) -> float:
    """The (1 - alpha)(1 + 1/n) empirical quantile that gives the marginal
    coverage guarantee."""
    n = scores.shape[0]
    if n == 0:
        raise ValueError("calibration set must be non-empty")
    level = math.ceil((n + 1) * (1.0 - alpha)) / n
    level = min(level, 1.0)
    return float(np.quantile(scores, level, method="higher"))


def calibrate(probs: Sequence[Sequence[float]],
              labels: Sequence[int],
              *, alpha: float = 0.1,
              score_fn: ScoreFn = "aps") -> ConformalCalibration:
    """Compute the conformal threshold from a calibration set."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    p = _validate(probs)
    y = np.asarray(labels, dtype=int)
    if y.shape[0] != p.shape[0]:
        raise ValueError("probs and labels must have matching row counts")
    if (y < 0).any() or (y >= p.shape[1]).any():
        raise ValueError("labels must index into the class axis")
    if score_fn == "aps":
        s = _aps_scores(p, y)
    elif score_fn == "lac":
        s = _lac_scores(p, y)
    else:
        raise ValueError(f"unknown score_fn: {score_fn!r}")
    q = _quantile_at_finite_correction(s, alpha)
    return ConformalCalibration(
        score_fn=score_fn, alpha=alpha, quantile=q,
        n_calibration=int(p.shape[0]),
    )


def predict_sets(probs: Sequence[Sequence[float]],
                 calibration: ConformalCalibration,
                 *, name: str = "conformal",
                 warn_size: float | None = None,
                 alert_size: float | None = None) -> ConformalResult:
    """Return prediction sets (as lists of class indices) for *probs*.

    ``warn_size`` / ``alert_size`` set severity bands on the **mean** set
    size. Defaults: warn at >2 classes, alert at >max(2, K/2)."""
    p = _validate(probs)
    n, K = p.shape
    if warn_size is None:
        warn_size = 2.0
    if alert_size is None:
        alert_size = max(2.0, K / 2.0)

    if calibration.score_fn == "aps":
        order = np.argsort(-p, axis=1)
        sorted_p = np.take_along_axis(p, order, axis=1)
        cumsum = np.cumsum(sorted_p, axis=1)
        keep = cumsum <= calibration.quantile
        # Always include at least the top-1 class.
        keep[:, 0] = True
        sets: list[list[int]] = []
        for i in range(n):
            sets.append(sorted(order[i, keep[i]].tolist()))
    elif calibration.score_fn == "lac":
        sets = [
            sorted(np.where((1.0 - p[i]) <= calibration.quantile)[0].tolist())
            for i in range(n)
        ]
        # never return an empty set — fall back to top-1
        for i, s in enumerate(sets):
            if not s:
                sets[i] = [int(np.argmax(p[i]))]
    else:
        raise ValueError(f"unknown score_fn: {calibration.score_fn!r}")

    sizes = np.array([len(s) for s in sets], dtype=float)
    mean_size = float(sizes.mean())
    if mean_size >= alert_size:
        severity = "alert"
    elif mean_size >= warn_size:
        severity = "warn"
    else:
        severity = "ok"

    return ConformalResult(
        name=name, value=mean_size, severity=severity, sets=sets,
        extra={
            "alpha": calibration.alpha,
            "score_fn": calibration.score_fn,
            "quantile": calibration.quantile,
            "n_classes": int(K),
            "max_size": int(sizes.max()) if n else 0,
            "frac_singleton": float((sizes == 1).mean()) if n else 0.0,
        },
    )


def empirical_coverage(probs: Sequence[Sequence[float]],
                       labels: Sequence[int],
                       calibration: ConformalCalibration) -> float:
    """Fraction of test labels actually contained in the predicted set."""
    res = predict_sets(probs, calibration)
    y = np.asarray(labels, dtype=int)
    return float(np.mean([y[i] in res.sets[i] for i in range(len(y))]))


__all__ = [
    "ConformalCalibration",
    "ConformalResult",
    "ScoreFn",
    "calibrate",
    "empirical_coverage",
    "predict_sets",
]
