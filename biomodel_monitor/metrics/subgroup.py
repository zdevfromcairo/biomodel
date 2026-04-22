"""Subgroup slicing with small-N-aware Wilson confidence intervals."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class SubgroupResult:
    dimension: str
    value: str
    n: int
    metric: str
    metric_value: float
    global_value: float
    delta: float
    ci: tuple[float, float] | None = None
    severity: str = "ok"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "n": self.n,
            "metric": self.metric,
            "metric_value": self.metric_value,
            "global_value": self.global_value,
            "delta": self.delta,
            "ci": list(self.ci) if self.ci is not None else None,
            "severity": self.severity,
            "extra": self.extra,
        }


def wilson_interval(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    center = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    lo = (center - half) / denom
    hi = (center + half) / denom
    return (max(0.0, lo), min(1.0, hi))


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if y_true.size == 0:
        return float("nan")
    return float(np.mean(y_true == y_pred))


def _binary_from_score(scores: np.ndarray, threshold: float) -> np.ndarray:
    return (scores >= threshold).astype(int)


def slice_metrics(
    *,
    dimension: str,
    groups: Sequence[str | None],
    scores: Sequence[float],
    labels: Sequence[int] | None = None,
    threshold: float = 0.5,
    min_n: int = 30,
    metric: str = "accuracy",
    top_k: int | None = None,
) -> list[SubgroupResult]:
    """Compute per-slice metrics, with small-N guard and Wilson CI.

    metric: ``"accuracy"`` (requires labels) or ``"mean_score"``.
    Returns slices flagged by severity:
        - ok    : delta within tolerance
        - warn  : |delta| >= 0.05 (and statistically meaningful)
        - alert : |delta| >= 0.10
    Slices with n < ``min_n`` are returned with severity "ok" and a note.
    """
    g = np.asarray(groups, dtype=object)
    s = np.asarray(scores, dtype=float)
    if g.shape[0] != s.shape[0]:
        raise ValueError("groups and scores must have equal length")
    y: np.ndarray | None
    if metric == "accuracy":
        if labels is None:
            raise ValueError("metric='accuracy' requires labels")
        y = np.asarray(labels, dtype=int)
        if y.shape[0] != s.shape[0]:
            raise ValueError("labels must align with scores")
        preds = _binary_from_score(s, threshold)
        global_value = _accuracy(y, preds)
    elif metric == "mean_score":
        y = None
        global_value = float(np.mean(s)) if s.size else float("nan")
    else:
        raise ValueError(f"Unsupported metric {metric!r}")

    results: list[SubgroupResult] = []
    unique_vals = sorted({str(v) if v is not None else "(unknown)" for v in g})
    for v in unique_vals:
        mask = np.array([(str(x) if x is not None else "(unknown)") == v for x in g])
        n = int(mask.sum())
        if metric == "accuracy":
            preds_v = _binary_from_score(s[mask], threshold)
            value = _accuracy(y[mask], preds_v)  # type: ignore[index]
            successes = int(np.sum(y[mask] == preds_v))  # type: ignore[index]
            ci = wilson_interval(successes, n) if n > 0 else None
        else:
            value = float(np.mean(s[mask])) if n > 0 else float("nan")
            ci = None
        delta = value - global_value if not math.isnan(value) else 0.0
        if n < min_n:
            sev = "ok"
            extra = {"note": f"sample size {n} below min_n={min_n}; CI may be unreliable"}
        else:
            abs_d = abs(delta)
            if abs_d >= 0.10:
                sev = "alert"
            elif abs_d >= 0.05:
                sev = "warn"
            else:
                sev = "ok"
            extra = {}
        results.append(
            SubgroupResult(
                dimension=dimension,
                value=v,
                n=n,
                metric=metric,
                metric_value=float(value) if not math.isnan(value) else float("nan"),
                global_value=float(global_value) if not math.isnan(global_value) else float("nan"),
                delta=float(delta),
                ci=ci,
                severity=sev,
                extra=extra,
            )
        )
    # Sort worst-first: alerts before warns; within a severity, most negative
    # delta first (underperformance), then largest |delta|.
    sev_rank = {"alert": 0, "warn": 1, "ok": 2}
    results.sort(key=lambda r: (sev_rank[r.severity], r.delta, -abs(r.delta)))
    if top_k is not None:
        return results[:top_k]
    return results


def slice_by_dimensions(
    *,
    record_dimensions: dict[str, Sequence[str | None]],
    scores: Sequence[float],
    labels: Sequence[int] | None = None,
    **kwargs,
) -> list[SubgroupResult]:
    """Compute slices across multiple metadata dimensions.

    ``record_dimensions`` maps dimension name -> list of per-record values.
    """
    out: list[SubgroupResult] = []
    for dim, groups in record_dimensions.items():
        out.extend(
            slice_metrics(
                dimension=dim,
                groups=groups,
                scores=scores,
                labels=labels,
                **kwargs,
            )
        )
    return out


def to_dimensions(records: Iterable, dimensions: Sequence[str]) -> dict[str, list]:
    """Helper: extract per-record values for the named metadata dimensions."""
    out: dict[str, list] = {d: [] for d in dimensions}
    for r in records:
        for d in dimensions:
            out[d].append(getattr(r, d, None))
    return out
