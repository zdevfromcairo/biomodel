"""Split-conformal prediction intervals + empirical-coverage monitoring (v0.6).

Conformal prediction wraps any black-box model and outputs prediction
intervals (regression) or prediction sets (classification) with a
**distribution-free, finite-sample coverage guarantee**: at level
``1 - alpha``, the *expected* miscoverage is exactly ``alpha``.

This module implements the two operations a monitor actually needs:

* :func:`fit_split_conformal_regression` — calibrate the conformity score on
  a held-out calibration set and return the radius ``q_hat``.
* :func:`empirical_coverage` — given live predictions + ground truth +
  intervals, compute the empirical coverage and flag drift in coverage as a
  monitor signal.

Why this matters for medical AI: a model can stay accurate on average while
its uncertainty becomes systematically wrong (e.g. *over-confident on a new
scanner*). Coverage drift catches that *before* a calibration metric does.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class ConformalCalibration:
    alpha: float
    q_hat: float  # additive radius for symmetric absolute-error intervals
    n_calibration: int
    method: Literal["abs_error"] = "abs_error"

    def interval(self, y_hat: float) -> tuple[float, float]:
        return (y_hat - self.q_hat, y_hat + self.q_hat)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoverageResult:
    """Empirical coverage observed in a live window."""

    nominal_coverage: float        # e.g. 0.9 for alpha=0.1
    empirical_coverage: float      # fraction of y in [lower, upper]
    n: int
    miscoverage_gap: float          # nominal - empirical
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def fit_split_conformal_regression(
    y_true_cal: list[float],
    y_pred_cal: list[float],
    *,
    alpha: float = 0.1,
) -> ConformalCalibration:
    """Calibrate a symmetric absolute-error conformal radius.

    Returns the smallest ``q_hat`` such that
    ``P(|y - y_hat| <= q_hat) >= 1 - alpha`` on the calibration set, using
    the standard finite-sample-corrected quantile
    ``ceil((n+1)(1-alpha)) / n``.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie in (0, 1)")
    if len(y_true_cal) != len(y_pred_cal):
        raise ValueError("y_true_cal and y_pred_cal must have the same length")
    n = len(y_true_cal)
    if n < 2:
        raise ValueError("need >= 2 calibration points")
    scores = sorted(abs(yt - yp) for yt, yp in zip(y_true_cal, y_pred_cal, strict=False))
    # Finite-sample correction.
    rank = math.ceil((n + 1) * (1 - alpha))
    rank = max(1, min(rank, n))
    q_hat = scores[rank - 1]
    return ConformalCalibration(alpha=alpha, q_hat=q_hat, n_calibration=n)


def empirical_coverage(
    y_true: list[float],
    intervals: list[tuple[float, float]],
    *,
    nominal_coverage: float,
    warn_gap: float = 0.05,
    alert_gap: float = 0.10,
    min_n: int = 30,
) -> CoverageResult:
    """Compute empirical coverage and assign a severity.

    ``warn_gap`` / ``alert_gap`` are absolute under-coverage thresholds:
    e.g. nominal=0.9, warn_gap=0.05 ⇒ warn if empirical < 0.85.

    Over-coverage (intervals too wide) is *not* alerted on but is reported.
    """
    if len(y_true) != len(intervals):
        raise ValueError("y_true and intervals must have the same length")
    n = len(y_true)
    if n == 0:
        raise ValueError("empirical_coverage needs at least 1 point")
    covered = sum(
        1 for y, (lo, hi) in zip(y_true, intervals, strict=False) if lo <= y <= hi
    )
    emp = covered / n
    gap = nominal_coverage - emp
    if n < min_n:
        sev: Literal["ok", "warn", "alert"] = "warn"
        note = f"low-N ({n} < {min_n})"
    elif gap >= alert_gap:
        sev, note = "alert", "severe under-coverage"
    elif gap >= warn_gap:
        sev, note = "warn", "moderate under-coverage"
    else:
        sev, note = "ok", "within tolerance"
    return CoverageResult(
        nominal_coverage=nominal_coverage,
        empirical_coverage=emp,
        n=n,
        miscoverage_gap=gap,
        severity=sev,
        extra={"covered": covered, "warn_gap": warn_gap, "alert_gap": alert_gap,
               "note": note},
    )
