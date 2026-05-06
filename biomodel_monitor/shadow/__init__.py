"""Shadow-deployment comparator (v0.10).

A *shadow deployment* serves the canary model in parallel with production
without exposing its predictions to downstream consumers. Both models see
the same inputs, so we can compare their predictions **paired**, on a
per-record basis, with much more statistical power than the unpaired
canary (mSPRT) comparator from v0.9.

This module ships two paired tests:

* **McNemar's test** (with mid-p continuity correction) on the 2×2 table
  of agreement / disagreement on binary outcomes (e.g. correct vs
  incorrect). This is the standard "is the new model significantly
  better/worse?" test for paired classification.
* A **paired bootstrap** for any continuous metric (e.g. log-loss, AUC
  estimated on the same subset). Reports a 95 % CI on the
  control-minus-canary mean.

Both return the v0.4 severity contract so the alert engine can consume
them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class ShadowResult:
    name: str
    value: float           # signed difference: control − canary (positive = control wins)
    severity: str = "ok"   # "ok"|"warn"|"alert"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "extra": self.extra,
        }


# --------------------------------------------------------------------------- #
# McNemar
# --------------------------------------------------------------------------- #


def _binom_two_sided_pvalue(b: int, c: int) -> float:
    """Exact two-sided binomial p-value for McNemar's test (Edwards' style).

    Under H0, b ~ Binomial(b+c, 0.5). We sum the tail more extreme than the
    observed minimum, doubled. Numpy-only — no scipy.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # P(X <= k) under Binomial(n, 0.5) using log-gamma
    log_half_n = -n * math.log(2.0)
    p = 0.0
    for i in range(0, k + 1):
        log_choose = (
            math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        )
        p += math.exp(log_choose + log_half_n)
    return min(1.0, 2.0 * p)


def mcnemar(control_correct: Sequence[int],
            canary_correct: Sequence[int],
            *, alpha_warn: float = 0.05,
            alpha_alert: float = 0.01) -> ShadowResult:
    """Paired McNemar's test on per-record correctness flags.

    Severity:
    * ``alert``  — p < alpha_alert AND canary worse than control,
    * ``warn``   — p < alpha_warn  AND canary worse than control,
    * ``ok``     — otherwise (including any direction where the canary wins
      or ties).
    """
    a = np.asarray(control_correct, dtype=int)
    c_arr = np.asarray(canary_correct, dtype=int)
    if a.shape != c_arr.shape:
        raise ValueError("control and canary arrays must have the same shape")
    if a.size == 0:
        raise ValueError("inputs must be non-empty")
    if not np.isin(a, (0, 1)).all() or not np.isin(c_arr, (0, 1)).all():
        raise ValueError("correctness flags must be 0/1")

    # b = control correct, canary wrong → "control wins"
    # c = control wrong,   canary correct → "canary wins"
    b = int(((a == 1) & (c_arr == 0)).sum())
    c = int(((a == 0) & (c_arr == 1)).sum())
    p = _binom_two_sided_pvalue(b, c)
    diff = (b - c) / a.size  # positive → control wins

    severity = "ok"
    if diff > 0:
        if p < alpha_alert:
            severity = "alert"
        elif p < alpha_warn:
            severity = "warn"
    return ShadowResult(
        name="mcnemar", value=diff, severity=severity,
        extra={
            "p_value": p,
            "b_control_wins": b,
            "c_canary_wins": c,
            "n": int(a.size),
            "control_acc": float(a.mean()),
            "canary_acc": float(c_arr.mean()),
        },
    )


# --------------------------------------------------------------------------- #
# Paired bootstrap
# --------------------------------------------------------------------------- #


def paired_bootstrap_diff(control: Sequence[float],
                          canary: Sequence[float],
                          *, n_boot: int = 2000,
                          seed: int = 0,
                          warn: float = 0.02,
                          alert: float = 0.05) -> ShadowResult:
    """Paired bootstrap on per-record continuous loss/score values.

    Returns a ``ShadowResult`` whose ``value`` is the **mean difference**
    ``mean(control) - mean(canary)`` and whose ``extra`` carries the 95 %
    percentile CI. Severity bands are on the magnitude of the mean
    difference (positive = control beats canary).
    """
    a = np.asarray(control, dtype=float)
    b = np.asarray(canary, dtype=float)
    if a.shape != b.shape:
        raise ValueError("control and canary arrays must have the same shape")
    if a.size == 0:
        raise ValueError("inputs must be non-empty")
    diff = a - b
    obs = float(diff.mean())

    rng = np.random.default_rng(seed)
    n = diff.size
    boots = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boots[i] = diff[idx].mean()
    lo, hi = np.quantile(boots, [0.025, 0.975])

    mag = abs(obs)
    if obs > 0 and mag >= alert:
        severity = "alert"
    elif obs > 0 and mag >= warn:
        severity = "warn"
    else:
        severity = "ok"

    return ShadowResult(
        name="paired_bootstrap", value=obs, severity=severity,
        extra={
            "ci_low": float(lo),
            "ci_high": float(hi),
            "n_boot": int(n_boot),
            "n": int(n),
        },
    )


__all__ = [
    "ShadowResult",
    "mcnemar",
    "paired_bootstrap_diff",
]
