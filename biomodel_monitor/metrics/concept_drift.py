"""Concept drift via PCA reconstruction error (v0.6).

When a model's *inputs* drift in ways that aren't visible in any single
feature's marginal (e.g. a new scanner shifts the joint distribution of
texture features), univariate drift tests miss it. The classical
multivariate trick is:

1. Fit PCA on a baseline feature matrix; keep the top ``k`` components.
2. For each new record, compute the **reconstruction error** when projecting
   to that ``k``-dim subspace and back.
3. The baseline distribution of reconstruction errors gives a one-sided
   threshold (e.g. 99th percentile). Live records exceeding the threshold
   are *concept-drift candidates*.

Implementation notes:

* Pure numpy (no scikit-learn dependency).
* Uses the SVD of the centered feature matrix — numerically stable and
  doesn't require ``X^T X`` to be well-conditioned.
* All metrics return a :class:`ConceptDriftResult` matching the project's
  ``severity`` / ``as_dict()`` contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np


@dataclass
class PCABasis:
    mean: list[float]
    components: list[list[float]]  # shape (k, d)
    explained_variance_ratio: list[float]
    baseline_threshold: float       # reconstruction-error cutoff
    n_baseline: int
    feature_names: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConceptDriftResult:
    n: int
    n_drift: int
    drift_rate: float
    threshold: float
    p95_error: float
    max_error: float
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def fit_pca_baseline(
    X: np.ndarray,
    *,
    n_components: int = 4,
    feature_names: list[str] | None = None,
    cutoff_quantile: float = 0.99,
) -> PCABasis:
    """Fit a PCA basis and pick a reconstruction-error cutoff.

    ``X`` is shape ``(n, d)``. Records with reconstruction error above
    ``cutoff_quantile`` of the baseline distribution will be flagged.
    """
    if X.ndim != 2:
        raise ValueError("X must be 2-D (n, d)")
    n, d = X.shape
    if n < max(2, n_components + 1):
        raise ValueError("need at least n_components + 1 baseline rows")
    if not 0 < cutoff_quantile < 1:
        raise ValueError("cutoff_quantile must lie in (0, 1)")
    k = min(n_components, d, n - 1)

    mean = X.mean(axis=0)
    Xc = X - mean
    # SVD: Xc = U S Vt
    _, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    components = Vt[:k]                    # (k, d)
    var = (S ** 2) / max(1, n - 1)
    total = float(var.sum()) or 1.0
    evr = (var[:k] / total).tolist()

    # Reconstruction errors on baseline.
    errs = _reconstruct_errors(Xc, components)
    threshold = float(np.quantile(errs, cutoff_quantile))

    return PCABasis(
        mean=mean.tolist(),
        components=components.tolist(),
        explained_variance_ratio=evr,
        baseline_threshold=threshold,
        n_baseline=n,
        feature_names=list(feature_names) if feature_names else [f"f{i}" for i in range(d)],
    )


def detect_concept_drift(
    X: np.ndarray,
    basis: PCABasis,
    *,
    warn_rate: float = 0.05,
    alert_rate: float = 0.15,
) -> ConceptDriftResult:
    """Score new records against a fitted baseline.

    The drift *rate* is the fraction of new records whose reconstruction
    error exceeds the baseline cutoff.
    """
    if X.ndim != 2:
        raise ValueError("X must be 2-D (n, d)")
    if X.shape[1] != len(basis.mean):
        raise ValueError(
            f"feature count mismatch: got {X.shape[1]}, basis was fit on {len(basis.mean)}",
        )
    Xc = X - np.array(basis.mean)
    components = np.array(basis.components)
    errs = _reconstruct_errors(Xc, components)
    n = len(errs)
    n_drift = int(np.sum(errs > basis.baseline_threshold))
    rate = n_drift / n if n else 0.0
    if rate >= alert_rate:
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif rate >= warn_rate:
        sev = "warn"
    else:
        sev = "ok"
    return ConceptDriftResult(
        n=n,
        n_drift=n_drift,
        drift_rate=rate,
        threshold=basis.baseline_threshold,
        p95_error=float(np.quantile(errs, 0.95)) if n else 0.0,
        max_error=float(np.max(errs)) if n else 0.0,
        severity=sev,
        extra={"warn_rate": warn_rate, "alert_rate": alert_rate, "k": len(basis.components)},
    )


def _reconstruct_errors(Xc: np.ndarray, components: np.ndarray) -> np.ndarray:
    """L2 reconstruction error per row given centered data and components."""
    # project: (n, k) ; reconstruct: (n, d)
    proj = Xc @ components.T
    rec = proj @ components
    diffs = Xc - rec
    return np.sqrt(np.einsum("ij,ij->i", diffs, diffs))
