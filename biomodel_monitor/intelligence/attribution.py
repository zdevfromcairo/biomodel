"""Per-alert root-cause attribution.

Given an alert (e.g. *"output score drift"*) and the records that contributed
to the run, decompose the headline metric into per-dimension contributions so
the operator can answer *"which site / scanner / cohort caused this?"*.

The algorithm:

* For each candidate dimension (``site_id``, ``scanner_id``, ``stain``,
  ``tissue_type``, ``cohort``) and each value, recompute the metric on the
  subset of records belonging to that value.
* Rank the values by their distance from the pooled metric, weighted by the
  share of records they cover.
* Return the top contributors as an :class:`AlertAttribution`.

This is *correlative* attribution, not causal — but it tells you exactly where
to look first, which is what most incident response actually needs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from biomodel_monitor.metrics.drift import psi
from biomodel_monitor.schema.models import PredictionRecord

DEFAULT_DIMENSIONS = ("site_id", "scanner_id", "stain", "tissue_type", "cohort")


@dataclass
class Contribution:
    dimension: str
    value: str
    n: int
    metric: float
    share: float
    delta_vs_pooled: float

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension, "value": self.value, "n": self.n,
            "metric": self.metric, "share": self.share,
            "delta_vs_pooled": self.delta_vs_pooled,
        }


@dataclass
class AlertAttribution:
    alert_key: str
    pooled_metric: float
    contributors: list[Contribution] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "alert_key": self.alert_key,
            "pooled_metric": self.pooled_metric,
            "contributors": [c.as_dict() for c in self.contributors],
        }


def _mean_score(records: list[PredictionRecord]) -> float:
    if not records:
        return 0.0
    return float(sum(r.score for r in records) / len(records))


def _drift_metric(
    baseline_scores: list[float],
    records: list[PredictionRecord],
) -> float:
    if not records or not baseline_scores:
        return 0.0
    return float(psi(baseline_scores, [r.score for r in records]).value)


def attribute_alert(
    alert_key: str,
    records: list[PredictionRecord],
    *,
    metric: Callable[[list[PredictionRecord]], float] | None = None,
    baseline_scores: list[float] | None = None,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    top_k: int = 5,
    min_n: int = 10,
) -> AlertAttribution:
    """Attribute ``alert_key`` to per-dimension/value contributions.

    By default the metric is mean predicted score. When ``baseline_scores`` is
    provided the metric becomes per-stratum PSI vs. the baseline scores — a
    more meaningful target for *output drift* alerts.
    """
    if metric is None:
        if baseline_scores is not None:
            def metric(rs: list[PredictionRecord], _b=baseline_scores) -> float:
                return _drift_metric(_b, rs)
        else:
            metric = _mean_score

    pooled = float(metric(records))
    n_total = len(records) or 1
    contribs: list[Contribution] = []

    for dim in dimensions:
        groups: dict[str, list[PredictionRecord]] = {}
        for r in records:
            v = getattr(r, dim, None)
            if v is None or v == "":
                continue
            groups.setdefault(str(v), []).append(r)
        for value, subset in groups.items():
            if len(subset) < min_n:
                continue
            m = float(metric(subset))
            contribs.append(Contribution(
                dimension=dim, value=value, n=len(subset),
                metric=m, share=len(subset) / n_total,
                delta_vs_pooled=m - pooled,
            ))

    contribs.sort(key=lambda c: -abs(c.delta_vs_pooled) * c.share)
    return AlertAttribution(
        alert_key=alert_key, pooled_metric=pooled, contributors=contribs[:top_k],
    )


__all__ = ["AlertAttribution", "Contribution", "attribute_alert"]
