"""Canary deployment monitor with mSPRT sequential testing (v0.9).

You're shipping a new model version next to the current production one. You
want to know — *as early as is statistically defensible* — whether the canary
is meaningfully worse so you can roll it back.

This module implements a **mixture sequential probability ratio test**
(mSPRT) on the difference of bounded outcomes (e.g. correctness 0/1, or any
metric clipped to ``[0, 1]``). Unlike a classical fixed-n test, mSPRT
controls Type-I error at any stopping time — you can peek as many times as
you like.

The implementation tracks two streams (control vs. canary), updates the
log-likelihood ratio under a normal mixture prior with variance ``tau^2``,
and reports a verdict in {``inconclusive``, ``promote``, ``rollback``}.

References:
* Howard, Ramdas, McAuliffe, Sekhon (2021), "Time-uniform Chernoff bounds via
  nonnegative supermartingales".
* Kohavi et al., A/B testing best practices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Verdict = str  # "inconclusive" | "promote" | "rollback"


@dataclass
class CanaryStats:
    n: int = 0
    sum: float = 0.0
    sum_sq: float = 0.0

    @property
    def mean(self) -> float:
        return self.sum / self.n if self.n else 0.0

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 0.0
        return max(0.0, (self.sum_sq - self.n * self.mean ** 2) / (self.n - 1))

    def add(self, x: float) -> None:
        self.n += 1
        self.sum += float(x)
        self.sum_sq += float(x) * float(x)


@dataclass
class CanaryDecision:
    verdict: Verdict
    log_lr: float
    diff_mean: float
    pooled_se: float
    threshold: float
    n_control: int
    n_canary: int
    severity: str = "ok"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "log_lr": self.log_lr,
            "diff_mean": self.diff_mean,
            "pooled_se": self.pooled_se,
            "threshold": self.threshold,
            "n_control": self.n_control,
            "n_canary": self.n_canary,
            "severity": self.severity,
            "extra": self.extra,
        }


class CanaryMonitor:
    """Sequential A/B canary monitor.

    Parameters
    ----------
    alpha:
        Family-wise Type-I error rate. Threshold = ``log(1/alpha)``.
    tau:
        Prior std-dev on the unknown effect size. Larger ``tau`` is more
        sensitive to large differences but slower to flag small ones.
    min_n:
        Minimum samples per arm before a verdict can fire (defends against
        early-stopping noise on tiny samples).
    rollback_only_if_worse:
        If True, the canary is rolled back only when its mean is below the
        control mean by a statistically significant margin (worse). When
        False, *any* significant difference triggers a verdict — useful
        when "different at all" is the operating concern.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.01,
        tau: float = 0.1,
        min_n: int = 30,
        rollback_only_if_worse: bool = True,
    ) -> None:
        if not 0 < alpha < 1:
            raise ValueError("alpha must be in (0, 1)")
        if tau <= 0:
            raise ValueError("tau must be > 0")
        self.alpha = float(alpha)
        self.tau = float(tau)
        self.min_n = int(min_n)
        self.rollback_only_if_worse = bool(rollback_only_if_worse)
        self.control = CanaryStats()
        self.canary = CanaryStats()

    @property
    def threshold(self) -> float:
        return math.log(1.0 / self.alpha)

    def add_control(self, x: float) -> None:
        self.control.add(x)

    def add_canary(self, x: float) -> None:
        self.canary.add(x)

    def _pooled_se(self) -> float:
        nc, nv = self.control.n, self.canary.n
        if nc < 2 or nv < 2:
            return 0.0
        # Pooled SE of the mean difference.
        var = (self.control.variance / nc) + (self.canary.variance / nv)
        return math.sqrt(var)

    def _log_lr(self) -> float:
        """Mixture-SPRT log-likelihood ratio for the mean difference.

        Under H0 the difference has mean 0; under H1 it has mean ~Normal(0,
        tau^2). Standard mSPRT closed form (Howard et al., eq. 6).
        """
        se = self._pooled_se()
        if se <= 0:
            return 0.0
        diff = self.canary.mean - self.control.mean
        z = diff / se
        v = se ** 2
        tau2 = self.tau ** 2
        # log( sqrt(v / (v + tau2)) * exp( z^2 * tau2 / (2 (v + tau2)) ) )
        return 0.5 * math.log(v / (v + tau2)) + (z * z * tau2) / (2.0 * (v + tau2))

    def decide(self) -> CanaryDecision:
        nc, nv = self.control.n, self.canary.n
        diff = self.canary.mean - self.control.mean
        se = self._pooled_se()
        log_lr = self._log_lr()
        verdict: Verdict = "inconclusive"
        sev = "ok"
        if nc >= self.min_n and nv >= self.min_n and log_lr >= self.threshold:
            if self.rollback_only_if_worse:
                if diff < 0:
                    verdict = "rollback"
                    sev = "alert"
                else:
                    verdict = "promote"
                    sev = "ok"
            else:
                verdict = "rollback" if diff < 0 else "promote"
                sev = "alert" if diff < 0 else "ok"
        return CanaryDecision(
            verdict=verdict,
            log_lr=log_lr,
            diff_mean=diff,
            pooled_se=se,
            threshold=self.threshold,
            n_control=nc,
            n_canary=nv,
            severity=sev,
            extra={
                "control_mean": self.control.mean,
                "canary_mean": self.canary.mean,
                "alpha": self.alpha,
                "tau": self.tau,
            },
        )


__all__ = ["CanaryDecision", "CanaryMonitor", "CanaryStats", "Verdict"]
