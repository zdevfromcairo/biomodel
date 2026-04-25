"""Fairness / equity metrics across demographic and operational strata.

These metrics share the dataclass / severity convention with the rest of the
package so they can be consumed by the alert engine without special-casing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FairnessResult:
    name: str
    dimension: str
    value: float
    severity: str
    n: int
    per_group: dict[str, dict[str, float]] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "dimension": self.dimension,
            "value": self.value,
            "severity": self.severity,
            "n": self.n,
            "per_group": self.per_group,
            **self.extra,
        }


def _confusion(y: np.ndarray, pred: np.ndarray) -> dict[str, int]:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def _safe_div(a: int, b: int) -> float:
    return float(a) / b if b > 0 else 0.0


def _severity_for_gap(gap: float) -> str:
    if abs(gap) >= 0.10:
        return "alert"
    if abs(gap) >= 0.05:
        return "warn"
    return "ok"


def demographic_parity(
    groups: Sequence[str],
    scores: Sequence[float],
    *,
    threshold: float = 0.5,
    dimension: str = "group",
    min_n: int = 30,
) -> FairnessResult:
    """Difference in positive-prediction rate across groups (max − min)."""
    g = np.asarray(groups)
    s = np.asarray(scores, dtype=float)
    if g.shape != s.shape:
        raise ValueError("groups and scores must have the same length")
    pred = (s >= threshold).astype(int)
    rates: dict[str, dict[str, float]] = {}
    for label in sorted(set(g.tolist())):
        m = g == label
        n = int(m.sum())
        if n < min_n:
            rates[str(label)] = {"n": n, "rate": float("nan"), "skipped": 1.0}
            continue
        rates[str(label)] = {"n": n, "rate": float(pred[m].mean())}
    valid = [v["rate"] for v in rates.values() if not np.isnan(v["rate"])]
    gap = float(max(valid) - min(valid)) if len(valid) >= 2 else 0.0
    return FairnessResult(
        name="demographic_parity_gap",
        dimension=dimension, value=gap, severity=_severity_for_gap(gap),
        n=int(g.size), per_group=rates,
        extra={"threshold": threshold, "min_n": min_n},
    )


def equal_opportunity(
    groups: Sequence[str],
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    threshold: float = 0.5,
    dimension: str = "group",
    min_n: int = 30,
) -> FairnessResult:
    """Difference in TPR across groups (a.k.a. equal-opportunity gap)."""
    g = np.asarray(groups)
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if not (g.shape == s.shape == y.shape):
        raise ValueError("groups, scores, labels must have the same length")
    pred = (s >= threshold).astype(int)
    per: dict[str, dict[str, float]] = {}
    for label in sorted(set(g.tolist())):
        m = g == label
        n = int(m.sum())
        cm = _confusion(y[m], pred[m])
        tpr = _safe_div(cm["tp"], cm["tp"] + cm["fn"])
        per[str(label)] = {"n": n, "tpr": tpr, **{k: float(v) for k, v in cm.items()}}
        if n < min_n:
            per[str(label)]["skipped"] = 1.0
    valid = [v["tpr"] for v in per.values() if v.get("skipped", 0.0) == 0.0]
    gap = float(max(valid) - min(valid)) if len(valid) >= 2 else 0.0
    return FairnessResult(
        name="equal_opportunity_gap",
        dimension=dimension, value=gap, severity=_severity_for_gap(gap),
        n=int(g.size), per_group=per,
        extra={"threshold": threshold, "min_n": min_n},
    )


def equalized_odds(
    groups: Sequence[str],
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    threshold: float = 0.5,
    dimension: str = "group",
    min_n: int = 30,
) -> FairnessResult:
    """Max(|ΔTPR|, |ΔFPR|) across groups — joint equal-opportunity + FPR parity."""
    g = np.asarray(groups)
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if not (g.shape == s.shape == y.shape):
        raise ValueError("groups, scores, labels must have the same length")
    pred = (s >= threshold).astype(int)
    per: dict[str, dict[str, float]] = {}
    for label in sorted(set(g.tolist())):
        m = g == label
        n = int(m.sum())
        cm = _confusion(y[m], pred[m])
        tpr = _safe_div(cm["tp"], cm["tp"] + cm["fn"])
        fpr = _safe_div(cm["fp"], cm["fp"] + cm["tn"])
        per[str(label)] = {"n": n, "tpr": tpr, "fpr": fpr}
        if n < min_n:
            per[str(label)]["skipped"] = 1.0
    valid = [v for v in per.values() if v.get("skipped", 0.0) == 0.0]
    if len(valid) < 2:
        gap = 0.0
    else:
        gap = float(max(
            max(v["tpr"] for v in valid) - min(v["tpr"] for v in valid),
            max(v["fpr"] for v in valid) - min(v["fpr"] for v in valid),
        ))
    return FairnessResult(
        name="equalized_odds_gap",
        dimension=dimension, value=gap, severity=_severity_for_gap(gap),
        n=int(g.size), per_group=per,
        extra={"threshold": threshold, "min_n": min_n},
    )
