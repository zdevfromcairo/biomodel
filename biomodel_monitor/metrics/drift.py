"""Drift metrics for tabular features, scores, categorical metadata, and embeddings.

All public metrics return a :class:`DriftResult` with a value, an optional
p-value or bootstrap CI, and a coarse severity label.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

Severity = str  # "ok" | "warn" | "alert"


def _severity(value: float, warn: float, alert: float) -> Severity:
    if value >= alert:
        return "alert"
    if value >= warn:
        return "warn"
    return "ok"


@dataclass
class DriftResult:
    name: str
    value: float
    severity: Severity = "ok"
    p_value: float | None = None
    ci: tuple[float, float] | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "p_value": self.p_value,
            "ci": list(self.ci) if self.ci is not None else None,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# PSI (Population Stability Index) for scalar/continuous features.
# ---------------------------------------------------------------------------


def psi(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    bins: int = 10,
    eps: float = 1e-6,
) -> DriftResult:
    """Population Stability Index between reference and current.

    PSI < 0.1   : no significant change.
    PSI 0.1-0.25: moderate change (warn).
    PSI > 0.25  : significant change (alert).
    """
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.size == 0 or cur.size == 0:
        raise ValueError("psi requires non-empty reference and current samples")
    # Use quantile bins from the reference distribution for robustness.
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(ref, quantiles))
    if edges.size < 2:
        # degenerate reference (constant); fall back to fixed bins
        edges = np.linspace(min(ref.min(), cur.min()), max(ref.max(), cur.max()) + eps, bins + 1)
    ref_hist, _ = np.histogram(ref, bins=edges)
    cur_hist, _ = np.histogram(cur, bins=edges)
    ref_pct = ref_hist / max(ref.size, 1) + eps
    cur_pct = cur_hist / max(cur.size, 1) + eps
    val = float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))
    return DriftResult(
        name="psi",
        value=val,
        severity=_severity(val, warn=0.1, alert=0.25),
        extra={"bins": int(edges.size - 1)},
    )


# ---------------------------------------------------------------------------
# Two-sample KS test for continuous distributions.
# ---------------------------------------------------------------------------


def ks_test(reference: Sequence[float], current: Sequence[float]) -> DriftResult:
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.size == 0 or cur.size == 0:
        raise ValueError("ks_test requires non-empty samples")
    res = stats.ks_2samp(ref, cur, alternative="two-sided", mode="auto")
    stat = float(res.statistic)
    p = float(res.pvalue)
    # Severity: tied to p-value with sensible defaults.
    if p < 0.001:
        sev = "alert"
    elif p < 0.05:
        sev = "warn"
    else:
        sev = "ok"
    return DriftResult(name="ks", value=stat, p_value=p, severity=sev)


# ---------------------------------------------------------------------------
# Jensen-Shannon divergence for categorical distributions.
# ---------------------------------------------------------------------------


def js_divergence_categorical(
    reference: Iterable, current: Iterable, *, eps: float = 1e-12
) -> DriftResult:
    """JS divergence (base-2, in [0, 1]) between empirical categorical PMFs."""
    ref = list(reference)
    cur = list(current)
    if not ref or not cur:
        raise ValueError("js_divergence_categorical requires non-empty samples")
    keys = sorted({*ref, *cur}, key=lambda x: str(x))
    ref_counts = np.array([ref.count(k) for k in keys], dtype=float)
    cur_counts = np.array([cur.count(k) for k in keys], dtype=float)
    p = ref_counts / ref_counts.sum() + eps
    q = cur_counts / cur_counts.sum() + eps
    p /= p.sum()
    q /= q.sum()
    m = 0.5 * (p + q)
    kl_pm = float(np.sum(p * np.log2(p / m)))
    kl_qm = float(np.sum(q * np.log2(q / m)))
    val = 0.5 * (kl_pm + kl_qm)
    val = max(0.0, min(1.0, val))
    return DriftResult(
        name="js_divergence",
        value=val,
        severity=_severity(val, warn=0.05, alert=0.15),
        extra={"categories": keys},
    )


# ---------------------------------------------------------------------------
# Maximum Mean Discrepancy with an RBF kernel — for embeddings.
# ---------------------------------------------------------------------------


def _median_heuristic_bandwidth(z: np.ndarray) -> float:
    n = z.shape[0]
    if n < 2:
        return 1.0
    idx = np.random.default_rng(0).choice(n, size=min(n, 256), replace=False)
    sub = z[idx]
    diffs = sub[:, None, :] - sub[None, :, :]
    sq = np.sum(diffs * diffs, axis=-1)
    upper = sq[np.triu_indices_from(sq, k=1)]
    med = float(np.median(upper))
    return med if med > 0 else 1.0


def mmd_rbf(
    reference: np.ndarray,
    current: np.ndarray,
    *,
    bandwidth: float | None = None,
    n_permutations: int = 100,
    rng: np.random.Generator | None = None,
) -> DriftResult:
    """Squared MMD with an RBF kernel + permutation p-value.

    Inputs are 2D arrays (n_samples, n_features).
    """
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.ndim != 2 or cur.ndim != 2 or ref.shape[1] != cur.shape[1]:
        raise ValueError("mmd_rbf expects two 2D arrays with matching feature dim")
    if ref.shape[0] < 2 or cur.shape[0] < 2:
        raise ValueError("mmd_rbf requires at least 2 samples per group")
    z = np.vstack([ref, cur])
    sigma2 = bandwidth if bandwidth is not None else _median_heuristic_bandwidth(z)

    def _k(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a[:, None, :] - b[None, :, :]
        return np.exp(-np.sum(d * d, axis=-1) / (2.0 * sigma2))

    def _mmd2(a: np.ndarray, b: np.ndarray) -> float:
        kxx = _k(a, a)
        kyy = _k(b, b)
        kxy = _k(a, b)
        n, m = a.shape[0], b.shape[0]
        # unbiased estimator
        np.fill_diagonal(kxx, 0.0)
        np.fill_diagonal(kyy, 0.0)
        return float(
            kxx.sum() / (n * (n - 1))
            + kyy.sum() / (m * (m - 1))
            - 2.0 * kxy.mean()
        )

    observed = _mmd2(ref, cur)
    rng = rng or np.random.default_rng(0)
    nge = 0
    for _ in range(n_permutations):
        perm = rng.permutation(z.shape[0])
        a = z[perm[: ref.shape[0]]]
        b = z[perm[ref.shape[0] :]]
        if _mmd2(a, b) >= observed:
            nge += 1
    p = (nge + 1) / (n_permutations + 1)
    sev = "alert" if p < 0.01 else "warn" if p < 0.05 else "ok"
    return DriftResult(
        name="mmd_rbf",
        value=observed,
        p_value=p,
        severity=sev,
        extra={"bandwidth": sigma2, "n_permutations": n_permutations},
    )


def embedding_cosine_shift(
    reference: np.ndarray, current: np.ndarray
) -> DriftResult:
    """Cosine distance between mean reference and mean current embedding."""
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.ndim != 2 or cur.ndim != 2 or ref.shape[1] != cur.shape[1]:
        raise ValueError("embedding_cosine_shift expects two 2D arrays")
    mu_r = ref.mean(axis=0)
    mu_c = cur.mean(axis=0)
    nr = np.linalg.norm(mu_r)
    nc = np.linalg.norm(mu_c)
    if nr == 0 or nc == 0:
        return DriftResult(name="cosine_shift", value=1.0, severity="alert")
    cos = float(np.dot(mu_r, mu_c) / (nr * nc))
    val = float(1.0 - cos)
    return DriftResult(
        name="cosine_shift",
        value=val,
        severity=_severity(val, warn=0.05, alert=0.20),
    )
