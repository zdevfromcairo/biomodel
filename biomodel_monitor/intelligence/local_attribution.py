"""Per-record (local) attribution for an alert (v0.8).

The v0.5 :func:`attribute_alert` ranks contributions at the
``(dimension, value)`` level. For incident response it's often more useful
to see *which individual records* most increase the alert's headline metric.
This module computes leave-one-out (LOO) influence for each record relative
to the pooled metric: if removing a record drops the metric by a lot, that
record is "pulling the alert".

This is O(n) for ``score_mean`` (closed form) and O(n) for any aggregator
that supports incremental sufficient statistics, so it scales to the typical
batch sizes seen in production (10²–10⁵).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from biomodel_monitor.schema.models import PredictionRecord


@dataclass
class RecordInfluence:
    record_index: int
    record_id: str | None
    score: float
    contribution: float  # signed: how much removing this record changes the metric
    abs_contribution: float
    site_id: str | None = None
    scanner_id: str | None = None
    cohort: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LocalAttribution:
    metric_name: str
    pooled: float
    n_records: int
    top_positive: list[RecordInfluence]
    top_negative: list[RecordInfluence]
    severity: str = "ok"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["top_positive"] = [r.as_dict() for r in self.top_positive]
        d["top_negative"] = [r.as_dict() for r in self.top_negative]
        return d


# ---------------------------------------------------------------------------
# Built-in aggregators with closed-form leave-one-out.
# ---------------------------------------------------------------------------

def _loo_mean(scores: list[float]) -> tuple[float, list[float]]:
    n = len(scores)
    if n == 0:
        return 0.0, []
    total = sum(scores)
    pooled = total / n
    if n == 1:
        return pooled, [0.0]
    deltas = [pooled - (total - s) / (n - 1) for s in scores]
    return pooled, deltas


def _loo_positive_rate(scores: list[float], threshold: float) -> tuple[float, list[float]]:
    n = len(scores)
    if n == 0:
        return 0.0, []
    flags = [1.0 if s >= threshold else 0.0 for s in scores]
    total = sum(flags)
    pooled = total / n
    if n == 1:
        return pooled, [0.0]
    deltas = [pooled - (total - f) / (n - 1) for f in flags]
    return pooled, deltas


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def attribute_local(
    records: Iterable[PredictionRecord],
    *,
    metric: str = "score_mean",
    threshold: float = 0.5,
    top_k: int = 5,
    warn_share: float = 0.20,
    alert_share: float = 0.40,
    custom_aggregator: Callable[[list[float]], tuple[float, list[float]]] | None = None,
) -> LocalAttribution:
    """Rank individual records by their leave-one-out contribution.

    Parameters
    ----------
    records:
        The records that make up the alerting batch.
    metric:
        ``"score_mean"`` (default) or ``"positive_rate"``.
    threshold:
        Decision threshold for ``positive_rate``.
    top_k:
        How many records to return on each side (positive / negative pull).
    warn_share, alert_share:
        Severity thresholds based on what fraction of the *absolute* total
        contribution mass the top-K accounts for. Low values are healthy
        (contributions are evenly spread); high values mean a small set
        of records explains most of the alert.
    custom_aggregator:
        Override for non-standard metrics; receives ``list[float]`` of scores
        and returns ``(pooled_value, [loo_delta_per_record])``.
    """
    recs = list(records)
    n = len(recs)
    if n == 0:
        return LocalAttribution(
            metric_name=metric, pooled=0.0, n_records=0,
            top_positive=[], top_negative=[], severity="ok",
            extra={"note": "empty input"},
        )
    scores = [float(r.score) for r in recs]
    if custom_aggregator is not None:
        pooled, deltas = custom_aggregator(scores)
    elif metric == "positive_rate":
        pooled, deltas = _loo_positive_rate(scores, threshold=threshold)
    elif metric == "score_mean":
        pooled, deltas = _loo_mean(scores)
    else:
        raise ValueError(f"unknown metric: {metric!r}")

    influences = [
        RecordInfluence(
            record_index=i,
            record_id=getattr(r, "record_id", None),
            score=scores[i],
            contribution=deltas[i],
            abs_contribution=abs(deltas[i]),
            site_id=getattr(r, "site_id", None),
            scanner_id=getattr(r, "scanner_id", None),
            cohort=getattr(r, "cohort", None),
        )
        for i, r in enumerate(recs)
    ]

    influences.sort(key=lambda x: x.contribution, reverse=True)
    top_positive = influences[:top_k]
    top_negative = list(reversed(influences[-top_k:]))

    total_abs = sum(x.abs_contribution for x in influences) or 1e-12
    top_share = sum(x.abs_contribution for x in (top_positive + top_negative)) / total_abs
    if top_share >= alert_share:
        sev = "alert"
    elif top_share >= warn_share:
        sev = "warn"
    else:
        sev = "ok"

    return LocalAttribution(
        metric_name=metric,
        pooled=float(pooled),
        n_records=n,
        top_positive=top_positive,
        top_negative=top_negative,
        severity=sev,
        extra={
            "top_share": top_share,
            "warn_share": warn_share,
            "alert_share": alert_share,
            "threshold": threshold if metric == "positive_rate" else None,
        },
    )


def aggregate_top_dimensions(
    influences: Iterable[RecordInfluence],
    *,
    dimensions: tuple[str, ...] = ("site_id", "scanner_id", "cohort"),
    top_k: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Roll per-record influences up to dimension/value totals.

    Useful when an attribution panel wants both the worst individual records
    *and* a one-line summary like *"86% of the negative pull is from
    site=BMC, scanner=Aperio-CS2"*.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    infs = list(influences)
    for d in dimensions:
        bucket: dict[str, dict[str, float]] = {}
        for inf in infs:
            v = getattr(inf, d, None)
            if v is None:
                continue
            entry = bucket.setdefault(str(v), {"contribution": 0.0, "n": 0.0})
            entry["contribution"] += inf.contribution
            entry["n"] += 1
        ranked = sorted(
            ({"value": k, **v} for k, v in bucket.items()),
            key=lambda x: abs(x["contribution"]), reverse=True,
        )[:top_k]
        if ranked:
            out[d] = ranked
    return out


__all__ = [
    "LocalAttribution",
    "RecordInfluence",
    "aggregate_top_dimensions",
    "attribute_local",
]
