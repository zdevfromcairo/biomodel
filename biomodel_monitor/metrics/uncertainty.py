"""Predictive uncertainty metrics (v0.7).

For models that emit a probability vector ``p`` over ``C`` classes, two
classical uncertainty measures are useful as **monitor signals**:

* **Predictive entropy** — ``-Σ p_c log p_c``. High when the model is
  unsure (prediction near uniform). A sustained rise in mean entropy is
  a textbook *epistemic-shift* warning.
* **Mutual information** (BALD) — when several MC-dropout / ensemble
  predictions are available, MI captures *epistemic* uncertainty: how
  much would knowing the true model parameters reduce the entropy?

This module exposes both, plus an :class:`UncertaintyResult` that wraps
the batch-level summary with the standard ``severity`` contract so it
slots into the existing alert engine.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np

LOG_EPS = 1e-12


@dataclass
class UncertaintyResult:
    n: int
    mean_entropy: float
    p95_entropy: float
    high_uncertainty_rate: float       # fraction with entropy > entropy_threshold
    entropy_threshold: float
    mean_mutual_information: float | None
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def predictive_entropy(probs: np.ndarray) -> np.ndarray:
    """Per-row entropy of a class-probability matrix.

    ``probs`` shape: ``(n, C)``. Rows are clipped & re-normalised so a
    model that emits exact zeros doesn't produce ``-inf``.
    """
    p = np.asarray(probs, dtype=float)
    if p.ndim != 2:
        raise ValueError("probs must be 2-D (n, C)")
    if (p < 0).any():
        raise ValueError("probs must be >= 0")
    p = np.clip(p, LOG_EPS, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    return -np.sum(p * np.log(p), axis=1)


def mutual_information(probs_per_member: np.ndarray) -> np.ndarray:
    """BALD per row from an ensemble's class-probability tensor.

    ``probs_per_member`` shape: ``(M, n, C)``. Returns shape ``(n,)``.
    """
    pm = np.asarray(probs_per_member, dtype=float)
    if pm.ndim != 3:
        raise ValueError("probs_per_member must be 3-D (M, n, C)")
    if (pm < 0).any():
        raise ValueError("probs must be >= 0")
    pm = np.clip(pm, LOG_EPS, 1.0)
    pm = pm / pm.sum(axis=2, keepdims=True)
    mean_p = pm.mean(axis=0)                                       # (n, C)
    h_mean = -np.sum(mean_p * np.log(np.clip(mean_p, LOG_EPS, 1.0)), axis=1)
    h_per = -np.sum(pm * np.log(pm), axis=2)                        # (M, n)
    mean_h = h_per.mean(axis=0)
    return h_mean - mean_h


def summarise_uncertainty(
    probs: np.ndarray,
    *,
    probs_per_member: np.ndarray | None = None,
    entropy_threshold: float | None = None,
    warn_high_rate: float = 0.20,
    alert_high_rate: float = 0.40,
) -> UncertaintyResult:
    """Summarise uncertainty over a batch of predictions.

    ``entropy_threshold`` defaults to ``0.5 * log(C)`` (half of the
    maximum possible entropy for ``C`` classes), which is a sensible
    "the model is genuinely unsure" cutoff.
    """
    p = np.asarray(probs, dtype=float)
    if p.ndim != 2:
        raise ValueError("probs must be 2-D (n, C)")
    if p.shape[0] == 0:
        raise ValueError("probs must not be empty")
    n, c = p.shape
    ent = predictive_entropy(p)
    thr = float(entropy_threshold) if entropy_threshold is not None \
        else 0.5 * math.log(max(c, 2))
    high_rate = float(np.mean(ent > thr))
    if high_rate >= alert_high_rate:
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif high_rate >= warn_high_rate:
        sev = "warn"
    else:
        sev = "ok"
    mi_mean: float | None = None
    if probs_per_member is not None:
        mi = mutual_information(probs_per_member)
        mi_mean = float(np.mean(mi))
    return UncertaintyResult(
        n=n,
        mean_entropy=float(np.mean(ent)),
        p95_entropy=float(np.quantile(ent, 0.95)),
        high_uncertainty_rate=high_rate,
        entropy_threshold=thr,
        mean_mutual_information=mi_mean,
        severity=sev,
        extra={"warn_high_rate": warn_high_rate,
               "alert_high_rate": alert_high_rate, "n_classes": c},
    )
