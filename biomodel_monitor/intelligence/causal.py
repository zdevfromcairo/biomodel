"""Causal / interaction-effect attribution (v0.6).

The v0.5 :func:`attribute_alert` ranks single ``(dimension, value)`` pairs
by their marginal contribution. That misses **interactions** — e.g.
"pathology-tumor-clf is broken specifically on (ScannerY × Stain=H&E),
not on either alone".

This module computes pairwise *interaction lift*:

    interaction = effect(d1=v1, d2=v2) - effect(d1=v1) - effect(d2=v2) + effect_pooled

Positive interaction means the joint subgroup deviates *more* than what its
marginal effects predict. We rank the top interactions and return them in a
shape that the existing `AlertAttribution` story can render.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

from biomodel_monitor.intelligence.attribution import DEFAULT_DIMENSIONS
from biomodel_monitor.schema.models import PredictionRecord


@dataclass
class Interaction:
    dim1: str
    value1: Any
    dim2: str
    value2: Any
    effect_joint: float
    effect_marginal_1: float
    effect_marginal_2: float
    effect_pooled: float
    interaction_lift: float
    n: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class InteractionAttribution:
    metric_name: str
    pooled: float
    top: list[Interaction]
    severity: str = "ok"
    notes: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["top"] = [t.as_dict() for t in self.top]
        return d


def _pull(rec: PredictionRecord, dim: str) -> Any:
    """Extract a dimension value, looking inside `features` if needed."""
    v = getattr(rec, dim, None)
    if v is None and rec.features is not None:
        v = rec.features.get(dim)
    return v


def _mean_score(records: Iterable[PredictionRecord]) -> float | None:
    vals = [r.score for r in records]
    return None if not vals else sum(vals) / len(vals)


def attribute_interactions(
    records: list[PredictionRecord],
    *,
    metric_name: str = "score_mean",
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    top_k: int = 5,
    min_n: int = 20,
    warn_lift: float = 0.05,
    alert_lift: float = 0.10,
) -> InteractionAttribution:
    """Rank pairwise interactions by their lift over additive marginals.

    Records with no value for a dimension are excluded from that dimension's
    aggregates. Joint subgroups smaller than ``min_n`` are skipped.
    """
    if not records:
        return InteractionAttribution(metric_name=metric_name, pooled=0.0, top=[],
                                      severity="ok", notes="empty input")

    pooled = _mean_score(records) or 0.0

    # Marginal means per dim/value.
    marginals: dict[tuple[str, Any], float] = {}
    for d in dimensions:
        groups: dict[Any, list[PredictionRecord]] = {}
        for r in records:
            v = _pull(r, d)
            if v is None:
                continue
            groups.setdefault(v, []).append(r)
        for v, recs in groups.items():
            m = _mean_score(recs)
            if m is not None:
                marginals[(d, v)] = m

    # Pairwise joints.
    interactions: list[Interaction] = []
    for d1, d2 in combinations(dimensions, 2):
        joint_groups: dict[tuple[Any, Any], list[PredictionRecord]] = {}
        for r in records:
            v1, v2 = _pull(r, d1), _pull(r, d2)
            if v1 is None or v2 is None:
                continue
            joint_groups.setdefault((v1, v2), []).append(r)
        for (v1, v2), recs in joint_groups.items():
            if len(recs) < min_n:
                continue
            joint_mean = _mean_score(recs) or 0.0
            m1 = marginals.get((d1, v1))
            m2 = marginals.get((d2, v2))
            if m1 is None or m2 is None:
                continue
            # Interaction lift relative to additive expectation.
            additive = m1 + m2 - pooled
            lift = joint_mean - additive
            interactions.append(Interaction(
                dim1=d1, value1=v1, dim2=d2, value2=v2,
                effect_joint=joint_mean, effect_marginal_1=m1,
                effect_marginal_2=m2, effect_pooled=pooled,
                interaction_lift=lift, n=len(recs),
            ))

    interactions.sort(key=lambda x: abs(x.interaction_lift), reverse=True)
    top = interactions[:top_k]

    if not top:
        sev = "ok"
        notes = "no interactions met min_n"
    else:
        worst = abs(top[0].interaction_lift)
        if worst >= alert_lift:
            sev = "alert"
        elif worst >= warn_lift:
            sev = "warn"
        else:
            sev = "ok"
        notes = None

    return InteractionAttribution(
        metric_name=metric_name, pooled=pooled, top=top, severity=sev,
        notes=notes,
        extra={"warn_lift": warn_lift, "alert_lift": alert_lift, "min_n": min_n},
    )
