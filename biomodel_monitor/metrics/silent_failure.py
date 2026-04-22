"""Silent failure signatures: the model looks fine but isn't."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class SilentFailureResult:
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


def _binary_entropy(p: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    p = np.clip(p, eps, 1.0 - eps)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def entropy_collapse(
    scores: Sequence[float],
    *,
    reference_mean_entropy: float | None = None,
    relative_drop_warn: float = 0.30,
    relative_drop_alert: float = 0.50,
) -> SilentFailureResult:
    """Detect collapse of predictive entropy vs. a reference mean entropy.

    If no reference is supplied, the absolute mean entropy is reported and
    severity is based on it being "unnaturally" low (< 0.2 / < 0.1).
    """
    s = np.asarray(scores, dtype=float)
    if s.size == 0:
        raise ValueError("entropy_collapse requires non-empty scores")
    h = _binary_entropy(s)
    mean_h = float(h.mean())
    if reference_mean_entropy is None:
        sev = "alert" if mean_h < 0.10 else "warn" if mean_h < 0.20 else "ok"
        return SilentFailureResult(
            name="entropy_collapse",
            value=mean_h,
            severity=sev,
            extra={"mode": "absolute"},
        )
    drop = (reference_mean_entropy - mean_h) / max(reference_mean_entropy, 1e-12)
    if drop >= relative_drop_alert:
        sev = "alert"
    elif drop >= relative_drop_warn:
        sev = "warn"
    else:
        sev = "ok"
    return SilentFailureResult(
        name="entropy_collapse",
        value=mean_h,
        severity=sev,
        extra={
            "mode": "relative",
            "relative_drop": drop,
            "reference_mean_entropy": reference_mean_entropy,
        },
    )


def confidence_accuracy_decoupling(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    threshold: float = 0.5,
    high_conf_cut: float = 0.90,
) -> SilentFailureResult:
    """Compare accuracy on high-confidence predictions vs. overall accuracy.

    A well-calibrated model is *more* accurate on its highest-confidence
    predictions. If that gap collapses or inverts, something is wrong.
    """
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if s.shape != y.shape or s.size == 0:
        raise ValueError("scores and labels must align and be non-empty")
    preds = (s >= threshold).astype(int)
    overall = float(np.mean(preds == y))
    high = (s >= high_conf_cut) | (s <= 1.0 - high_conf_cut)
    if high.sum() == 0:
        return SilentFailureResult(
            name="confidence_accuracy_decoupling",
            value=0.0,
            severity="ok",
            extra={"note": "no high-confidence predictions"},
        )
    high_acc = float(np.mean(preds[high] == y[high]))
    gap = high_acc - overall  # expected positive
    if gap < -0.05:
        sev = "alert"
    elif gap < 0.0:
        sev = "warn"
    else:
        sev = "ok"
    return SilentFailureResult(
        name="confidence_accuracy_decoupling",
        value=gap,
        severity=sev,
        extra={
            "overall_accuracy": overall,
            "high_confidence_accuracy": high_acc,
            "n_high_confidence": int(high.sum()),
        },
    )


def prediction_drift_without_input_drift(
    output_drift_severity: str,
    input_drift_severities: Sequence[str],
) -> SilentFailureResult:
    """Output distribution shifted but inputs look stable -> upstream change."""
    sev_rank = {"ok": 0, "warn": 1, "alert": 2}
    out_rank = sev_rank.get(output_drift_severity, 0)
    max_in = max((sev_rank.get(s, 0) for s in input_drift_severities), default=0)
    decoupled = out_rank >= 1 and max_in == 0
    sev = "alert" if out_rank == 2 and max_in == 0 else "warn" if decoupled else "ok"
    return SilentFailureResult(
        name="prediction_drift_without_input_drift",
        value=float(out_rank - max_in),
        severity=sev,
        extra={
            "output_drift_severity": output_drift_severity,
            "max_input_drift_severity": next(
                (k for k, v in sev_rank.items() if v == max_in), "ok"
            ),
        },
    )
