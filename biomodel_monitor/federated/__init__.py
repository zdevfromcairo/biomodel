"""Federated drift / calibration aggregation (v0.7).

When a model is deployed across many sites, you usually **cannot pool raw
records** — patient privacy, regulatory boundaries, or simply data
sovereignty get in the way. Each site, however, can publish *sufficient
statistics* that are safe to share:

* per-bin counts for histogram-based drift (PSI / KL / Hellinger),
* counts of binary outcomes per probability bin for ECE,
* count + sum + sum-of-squares for any continuous metric.

This module implements the aggregator that takes a list of these
:class:`SiteStats` and computes the **pooled** drift / calibration metrics
*as if* the underlying records had been concatenated, plus a per-site
contribution breakdown.

What's exposed:

* :class:`HistogramSummary` — counts per bin, ready to be sent.
* :class:`CalibrationSummary` — bin counts + bin-pos counts + bin-conf sums.
* :class:`MomentSummary` — n / sum / sum-of-squares for a continuous metric.
* :func:`aggregate_histograms` — pooled distribution + per-site PSI vs pool.
* :func:`aggregate_calibration` — pooled ECE + per-site contribution.
* :func:`aggregate_moments` — pooled mean + stdev + per-site z-score.
"""

from biomodel_monitor.federated.aggregator import (
    CalibrationSummary,
    FederatedCalibration,
    FederatedDrift,
    FederatedMoments,
    HistogramSummary,
    MomentSummary,
    aggregate_calibration,
    aggregate_histograms,
    aggregate_moments,
)

__all__ = [
    "CalibrationSummary",
    "FederatedCalibration",
    "FederatedDrift",
    "FederatedMoments",
    "HistogramSummary",
    "MomentSummary",
    "aggregate_calibration",
    "aggregate_histograms",
    "aggregate_moments",
]
