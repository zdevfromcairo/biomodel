"""Multi-class calibration metrics.

Generalises the binary ECE / Brier metrics in :mod:`metrics.calibration`.
Inputs are score matrices (n_samples, n_classes) and integer labels in
``[0, n_classes)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from biomodel_monitor.metrics.calibration import expected_calibration_error


@dataclass
class MulticlassCalibrationResult:
    name: str
    value: float
    severity: str
    n: int
    n_classes: int
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "n": self.n,
            "n_classes": self.n_classes,
            **self.extra,
        }


def _validate(scores: Sequence[Sequence[float]], labels: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if s.ndim != 2:
        raise ValueError("scores must be 2-D (n_samples, n_classes)")
    if s.shape[0] != y.shape[0]:
        raise ValueError("scores and labels must have the same length")
    if y.min() < 0 or y.max() >= s.shape[1]:
        raise ValueError("labels out of range for the score matrix")
    if not np.allclose(s.sum(axis=1), 1.0, atol=1e-3):
        # normalise softly — many models output unnormalised logits
        s = s / np.clip(s.sum(axis=1, keepdims=True), 1e-12, None)
    return s, y


def top_label_ece(
    scores: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    n_bins: int = 15,
) -> MulticlassCalibrationResult:
    """ECE on the top-1 confidence vs. top-1 correctness."""
    s, y = _validate(scores, labels)
    top_idx = s.argmax(axis=1)
    confidences = s[np.arange(s.shape[0]), top_idx]
    correct = (top_idx == y).astype(int).tolist()
    res = expected_calibration_error(confidences.tolist(), correct, n_bins=n_bins)
    return MulticlassCalibrationResult(
        name="top_label_ece", value=res.value, severity=res.severity,
        n=int(s.shape[0]), n_classes=int(s.shape[1]),
        extra={"binning": "uniform", "n_bins": n_bins},
    )


def class_wise_ece(
    scores: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    n_bins: int = 15,
) -> MulticlassCalibrationResult:
    """Mean over classes of one-vs-rest ECE (a.k.a. marginal ECE)."""
    s, y = _validate(scores, labels)
    eces: list[float] = []
    sevs: list[str] = []
    for k in range(s.shape[1]):
        cls_scores = s[:, k]
        cls_labels = (y == k).astype(int).tolist()
        if sum(cls_labels) == 0 or sum(cls_labels) == len(cls_labels):
            continue
        r = expected_calibration_error(cls_scores.tolist(), cls_labels, n_bins=n_bins)
        eces.append(r.value)
        sevs.append(r.severity)
    if not eces:
        return MulticlassCalibrationResult(
            name="class_wise_ece", value=0.0, severity="ok",
            n=int(s.shape[0]), n_classes=int(s.shape[1]),
            extra={"note": "insufficient class diversity"},
        )
    val = float(np.mean(eces))
    rank = {"ok": 0, "warn": 1, "alert": 2}
    sev = sorted(sevs, key=lambda x: -rank[x])[0]
    return MulticlassCalibrationResult(
        name="class_wise_ece", value=val, severity=sev,
        n=int(s.shape[0]), n_classes=int(s.shape[1]),
        extra={"per_class_ece": eces},
    )


def multiclass_brier(
    scores: Sequence[Sequence[float]],
    labels: Sequence[int],
) -> MulticlassCalibrationResult:
    """Multiclass Brier score = mean squared error vs. one-hot labels."""
    s, y = _validate(scores, labels)
    one_hot = np.zeros_like(s)
    one_hot[np.arange(s.shape[0]), y] = 1.0
    val = float(np.mean(np.sum((s - one_hot) ** 2, axis=1)))
    if val >= 0.5:
        sev = "alert"
    elif val >= 0.25:
        sev = "warn"
    else:
        sev = "ok"
    return MulticlassCalibrationResult(
        name="multiclass_brier", value=val, severity=sev,
        n=int(s.shape[0]), n_classes=int(s.shape[1]),
    )
