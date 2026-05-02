"""Differential-privacy noise for federated aggregation (v0.9).

When sites publish sufficient statistics for federated drift / calibration,
an adversary with prior knowledge can sometimes recover individual records
from those statistics. This module adds calibrated **Laplace noise** under
an :math:`(\\epsilon, 0)`-differential-privacy guarantee, with sequential
composition tracking via a :class:`PrivacyAccountant`.

For a histogram-style query the L1 sensitivity of *adding or removing one
record* is at most ``2`` (one bin gains 1, the previous bin loses 1 if a
record is reclassified — common upper bound for histogram release). For mean
queries on a bounded range ``[lo, hi]`` the sensitivity is ``(hi - lo) / n``.
The caller passes the appropriate sensitivity for their query.

This is a *small* DP utility — not a replacement for OpenDP — but it gives
sites a tractable, well-documented way to reduce reconstruction risk on the
specific statistics shipped by :mod:`biomodel_monitor.federated`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PrivacyAccountant:
    """Pure-DP sequential composition: epsilons add up."""

    budget_epsilon: float
    spent: float = 0.0
    history: list[dict] = field(default_factory=list)

    def remaining(self) -> float:
        return max(0.0, self.budget_epsilon - self.spent)

    def spend(self, epsilon: float, *, label: str = "") -> None:
        if epsilon <= 0:
            raise ValueError("epsilon must be > 0")
        if epsilon > self.remaining() + 1e-12:
            raise ValueError(
                f"privacy budget exhausted: requested ε={epsilon}, "
                f"remaining ε={self.remaining()}"
            )
        self.spent += float(epsilon)
        self.history.append({"epsilon": float(epsilon), "label": label,
                             "spent": self.spent})

    def as_dict(self) -> dict:
        return {
            "budget_epsilon": self.budget_epsilon,
            "spent": self.spent,
            "remaining": self.remaining(),
            "history": list(self.history),
        }


def laplace_noise(
    value: float | Sequence[float] | np.ndarray,
    *,
    sensitivity: float,
    epsilon: float,
    seed: int | None = None,
) -> float | np.ndarray:
    """Add Laplace noise calibrated to ``(sensitivity, epsilon)``.

    Mathematically: noise ~ Laplace(scale = sensitivity / epsilon).
    """
    if sensitivity <= 0:
        raise ValueError("sensitivity must be > 0")
    if epsilon <= 0:
        raise ValueError("epsilon must be > 0")
    rng = np.random.default_rng(seed)
    scale = sensitivity / epsilon
    arr = np.asarray(value, dtype=float)
    noise = rng.laplace(loc=0.0, scale=scale, size=arr.shape)
    out = arr + noise
    return float(out) if out.ndim == 0 else out


def privatise_histogram(
    counts: Sequence[int] | np.ndarray,
    *,
    epsilon: float,
    accountant: PrivacyAccountant | None = None,
    label: str = "histogram",
    seed: int | None = None,
) -> np.ndarray:
    """Release a histogram with ε-DP Laplace noise (sensitivity 2).

    The +2 sensitivity comes from the standard "swap one record" model where
    a single record can shift one count by +1 and another by -1.
    """
    if accountant is not None:
        accountant.spend(epsilon, label=label)
    arr = np.asarray(counts, dtype=float)
    noisy = laplace_noise(arr, sensitivity=2.0, epsilon=epsilon, seed=seed)
    # Counts can't be negative; clip post-hoc (this is a standard "post-
    # processing" step that doesn't reduce the privacy guarantee).
    return np.maximum(noisy, 0.0)


def privatise_mean(
    mean: float, *, n: int, lo: float, hi: float, epsilon: float,
    accountant: PrivacyAccountant | None = None,
    label: str = "mean",
    seed: int | None = None,
) -> float:
    """Release a bounded mean with ε-DP Laplace noise."""
    if n <= 0:
        raise ValueError("n must be > 0")
    if hi <= lo:
        raise ValueError("hi must be > lo")
    if accountant is not None:
        accountant.spend(epsilon, label=label)
    sensitivity = (hi - lo) / n
    return float(laplace_noise(mean, sensitivity=sensitivity,
                               epsilon=epsilon, seed=seed))


__all__ = [
    "PrivacyAccountant",
    "laplace_noise",
    "privatise_histogram",
    "privatise_mean",
]
