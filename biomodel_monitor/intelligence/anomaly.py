"""Robust anomaly score for metric history.

A simple median-absolute-deviation z-score (sometimes called *modified z-score*)
that is far less sensitive to outliers than the classical (mean,std) z-score.
That matters here because monitoring time series are often dominated by a
handful of bad days that we don't want to set the baseline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AnomalyResult:
    last_value: float
    score: float
    severity: str

    def as_dict(self) -> dict:
        return {
            "last_value": self.last_value,
            "score": self.score,
            "severity": self.severity,
        }


def robust_zscore(
    series: list[float] | np.ndarray,
    *,
    warn_threshold: float = 3.0,
    alert_threshold: float = 5.0,
) -> AnomalyResult:
    """Return a robust z-score for the last point in ``series``.

    Returns 0.0 for series shorter than 3 points (not enough signal).
    """
    arr = np.asarray(list(series), dtype=float)
    if arr.size < 3:
        return AnomalyResult(
            last_value=float(arr[-1]) if arr.size else 0.0, score=0.0, severity="ok",
        )
    last = float(arr[-1])
    history = arr[:-1]
    median = float(np.median(history))
    mad = float(np.median(np.abs(history - median)))
    # 1.4826 is the consistency constant for MAD under a Normal distribution
    sigma = 1.4826 * mad if mad > 0 else max(float(np.std(history, ddof=0)), 1e-9)
    score = abs(last - median) / sigma
    if score >= alert_threshold:
        severity = "alert"
    elif score >= warn_threshold:
        severity = "warn"
    else:
        severity = "ok"
    return AnomalyResult(last_value=last, score=float(score), severity=severity)


__all__ = ["AnomalyResult", "robust_zscore"]
