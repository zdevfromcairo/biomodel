"""Calibration metrics: ECE, MCE, Brier, reliability curves."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CalibrationResult:
    name: str
    value: float
    severity: str = "ok"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "extra": self.extra,
        }


def _validate(scores: Sequence[float], labels: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if s.size == 0:
        raise ValueError("calibration metrics require non-empty inputs")
    if s.shape != y.shape:
        raise ValueError("scores and labels must have the same shape")
    if np.any((s < 0) | (s > 1)):
        raise ValueError("scores must lie in [0, 1]")
    if not np.all(np.isin(y, (0, 1))):
        raise ValueError("labels must be binary 0/1 for these calibration metrics")
    return s, y


def reliability_curve(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    n_bins: int = 10,
    strategy: str = "uniform",
) -> dict:
    """Build a reliability curve.

    strategy: ``"uniform"`` (equal-width bins) or ``"quantile"`` (adaptive).
    """
    s, y = _validate(scores, labels)
    if strategy == "uniform":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    elif strategy == "quantile":
        edges = np.unique(np.quantile(s, np.linspace(0.0, 1.0, n_bins + 1)))
        if edges.size < 2:
            edges = np.array([0.0, 1.0])
    else:
        raise ValueError("strategy must be 'uniform' or 'quantile'")
    # bin index per sample
    idx = np.clip(np.digitize(s, edges[1:-1], right=False), 0, edges.size - 2)
    bin_conf: list[float] = []
    bin_acc: list[float] = []
    bin_n: list[int] = []
    for b in range(edges.size - 1):
        mask = idx == b
        n = int(mask.sum())
        bin_n.append(n)
        if n == 0:
            bin_conf.append(float("nan"))
            bin_acc.append(float("nan"))
        else:
            bin_conf.append(float(s[mask].mean()))
            bin_acc.append(float(y[mask].mean()))
    return {
        "edges": edges.tolist(),
        "bin_confidence": bin_conf,
        "bin_accuracy": bin_acc,
        "bin_count": bin_n,
    }


def expected_calibration_error(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    n_bins: int = 10,
    strategy: str = "uniform",
) -> CalibrationResult:
    s, y = _validate(scores, labels)
    curve = reliability_curve(s, y, n_bins=n_bins, strategy=strategy)
    n = s.size
    ece = 0.0
    for conf, acc, count in zip(
        curve["bin_confidence"], curve["bin_accuracy"], curve["bin_count"]
    ):
        if count == 0:
            continue
        ece += (count / n) * abs(conf - acc)
    if ece >= 0.10:
        sev = "alert"
    elif ece >= 0.05:
        sev = "warn"
    else:
        sev = "ok"
    return CalibrationResult(name="ece", value=float(ece), severity=sev, extra=curve)


def maximum_calibration_error(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    n_bins: int = 10,
    strategy: str = "uniform",
) -> CalibrationResult:
    curve = reliability_curve(scores, labels, n_bins=n_bins, strategy=strategy)
    diffs = [
        abs(c - a)
        for c, a, n in zip(curve["bin_confidence"], curve["bin_accuracy"], curve["bin_count"])
        if n > 0
    ]
    val = float(max(diffs)) if diffs else 0.0
    sev = "alert" if val >= 0.25 else "warn" if val >= 0.10 else "ok"
    return CalibrationResult(name="mce", value=val, severity=sev, extra=curve)


def brier_score(
    scores: Sequence[float], labels: Sequence[int]
) -> CalibrationResult:
    s, y = _validate(scores, labels)
    val = float(np.mean((s - y) ** 2))
    sev = "alert" if val >= 0.25 else "warn" if val >= 0.15 else "ok"
    return CalibrationResult(name="brier", value=val, severity=sev)
