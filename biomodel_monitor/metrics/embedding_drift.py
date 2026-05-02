"""Embedding-space drift via Maximum Mean Discrepancy (v0.8).

Univariate PSI/KS test only one feature at a time. Many real medical-AI drifts
are *multivariate* — a joint shift in (age, scanner, contrast) that no single
feature flags. The :func:`mmd_rbf` test computes the squared Maximum Mean
Discrepancy with an RBF (Gaussian) kernel between two embedding sets and
returns a permutation p-value. The bandwidth defaults to the median heuristic.

The result follows the project's metric-severity contract
(``severity`` ∈ {``ok``, ``warn``, ``alert``} + ``as_dict()``) so the
:class:`AlertEngine` can consume it directly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class MMDResult:
    name: str = "mmd_rbf"
    value: float = 0.0
    severity: str = "ok"
    p_value: float | None = None
    bandwidth: float | None = None
    n_reference: int = 0
    n_current: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "p_value": self.p_value,
            "bandwidth": self.bandwidth,
            "n_reference": self.n_reference,
            "n_current": self.n_current,
            "extra": self.extra,
        }


def _as_array(x: Sequence | np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if a.ndim != 2:
        raise ValueError(f"expected 1D or 2D embeddings, got shape {a.shape}")
    return a


def _median_heuristic(z: np.ndarray, *, max_pairs: int = 2000,
                      rng: np.random.Generator | None = None) -> float:
    """Median pairwise Euclidean distance, sub-sampled for speed."""
    n = z.shape[0]
    if n < 2:
        return 1.0
    rng = rng or np.random.default_rng(0)
    if n * (n - 1) // 2 > max_pairs:
        idx_a = rng.integers(0, n, size=max_pairs)
        idx_b = rng.integers(0, n, size=max_pairs)
        mask = idx_a != idx_b
        idx_a, idx_b = idx_a[mask], idx_b[mask]
        diffs = z[idx_a] - z[idx_b]
    else:
        diffs = z[:, None, :] - z[None, :, :]
        diffs = diffs.reshape(-1, z.shape[1])
    d = np.linalg.norm(diffs, axis=-1)
    d = d[d > 0]
    if d.size == 0:
        return 1.0
    return float(np.median(d))


def _rbf_kernel(a: np.ndarray, b: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        sigma = 1.0
    a2 = (a * a).sum(axis=1, keepdims=True)
    b2 = (b * b).sum(axis=1, keepdims=True).T
    cross = a @ b.T
    sq = a2 + b2 - 2.0 * cross
    np.maximum(sq, 0.0, out=sq)
    return np.exp(-sq / (2.0 * sigma * sigma))


def _mmd2_unbiased(kxx: np.ndarray, kyy: np.ndarray, kxy: np.ndarray) -> float:
    """Unbiased estimator of MMD^2 (Gretton et al. 2012, eq. 3)."""
    m = kxx.shape[0]
    n = kyy.shape[0]
    if m < 2 or n < 2:
        return 0.0
    sx = (kxx.sum() - np.trace(kxx)) / (m * (m - 1))
    sy = (kyy.sum() - np.trace(kyy)) / (n * (n - 1))
    sxy = kxy.sum() / (m * n)
    return float(sx + sy - 2.0 * sxy)


def mmd_rbf(
    reference: Sequence | np.ndarray,
    current: Sequence | np.ndarray,
    *,
    bandwidth: float | None = None,
    n_permutations: int = 200,
    warn: float = 0.05,
    alert: float = 0.10,
    seed: int = 0,
) -> MMDResult:
    """Squared MMD between two embedding sets with a permutation p-value.

    Parameters
    ----------
    reference, current:
        Either 1D sequences (treated as scalar embeddings) or 2D arrays of
        shape ``(n_samples, n_features)``.
    bandwidth:
        RBF bandwidth :math:`\\sigma`. If ``None``, set by the median
        heuristic on the pooled data.
    n_permutations:
        Number of label-shuffle permutations to estimate the null
        distribution. Set to 0 to skip and report only the statistic.
    warn, alert:
        Severity thresholds on the *MMD^2* value. Defaults reflect the
        small-but-meaningful and large effect sizes typical of clinical
        embedding shifts at moderate dimension.
    """
    ref = _as_array(reference)
    cur = _as_array(current)
    if ref.shape[1] != cur.shape[1]:
        raise ValueError("reference and current must have the same dimensionality")
    m, n = ref.shape[0], cur.shape[0]
    if m < 2 or n < 2:
        return MMDResult(value=0.0, severity="ok", p_value=None,
                         bandwidth=bandwidth, n_reference=m, n_current=n,
                         extra={"note": "insufficient samples (<2 in one side)"})

    rng = np.random.default_rng(seed)
    pooled = np.vstack([ref, cur])
    sigma = float(bandwidth) if bandwidth is not None else _median_heuristic(pooled, rng=rng)
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = 1.0

    kxx = _rbf_kernel(ref, ref, sigma)
    kyy = _rbf_kernel(cur, cur, sigma)
    kxy = _rbf_kernel(ref, cur, sigma)
    stat = _mmd2_unbiased(kxx, kyy, kxy)

    p_value: float | None = None
    if n_permutations and n_permutations > 0:
        kpp = _rbf_kernel(pooled, pooled, sigma)
        idx = np.arange(m + n)
        ge = 0
        for _ in range(int(n_permutations)):
            perm = rng.permutation(idx)
            la = perm[:m]   # new "reference" indices into pooled
            lb = perm[m:]   # new "current" indices into pooled
            kxx_p = kpp[np.ix_(la, la)]
            kyy_p = kpp[np.ix_(lb, lb)]
            kxy_p = kpp[np.ix_(la, lb)]
            stat_p = _mmd2_unbiased(kxx_p, kyy_p, kxy_p)
            if stat_p >= stat:
                ge += 1
        # +1 in numerator/denominator: standard Phipson-Smyth correction
        # so the p-value is never exactly zero.
        p_value = (ge + 1) / (int(n_permutations) + 1)

    if stat >= alert:
        sev = "alert"
    elif stat >= warn:
        sev = "warn"
    else:
        sev = "ok"
    if p_value is not None and p_value < 0.001 and sev == "ok":
        sev = "warn"

    return MMDResult(
        value=float(stat),
        severity=sev,
        p_value=p_value,
        bandwidth=float(sigma),
        n_reference=m,
        n_current=n,
        extra={
            "warn": warn, "alert": alert,
            "n_permutations": int(n_permutations),
            "kernel": "rbf",
        },
    )


__all__ = ["MMDResult", "mmd_rbf"]
