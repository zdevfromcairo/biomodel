"""Closed-loop feedback: recompute calibration once experts have labelled
records (v0.10).

This is the *output* end of the active-learning loop. Reviewers labelled
records via :mod:`biomodel_monitor.active_learning`; this module merges those
labels into the existing prediction batch (or a fresh score vector), and
reruns the v0.4 calibration metrics on the *now-fully-labelled* subset.

The merge rules are deliberately strict:

* Expert labels **always** win over any prior label on the same record.
* Records without an expert label are skipped — we don't want the
  recomputed metric to mix synthetic and human-validated labels.
* If no expert labels are available the function raises rather than
  silently returning the original metric (fail loud).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from biomodel_monitor.active_learning import QueueItem
from biomodel_monitor.metrics.calibration import (
    CalibrationResult,
    expected_calibration_error,
)


@dataclass
class FeedbackBatch:
    """Result of merging expert labels into a prediction batch."""

    n_total: int
    n_relabelled: int
    n_used: int

    def as_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_relabelled": self.n_relabelled,
            "n_used": self.n_used,
        }


def _coerce_label(label: int | str | None) -> int | None:
    if label is None:
        return None
    if isinstance(label, int):
        return label
    try:
        return int(label)
    except ValueError:
        return None


def merge_labels(record_ids: Sequence[str],
                 scores: Sequence[float],
                 prior_labels: Sequence[int | None],
                 expert_items: Iterable[QueueItem]) -> tuple[
                     list[float], list[int], FeedbackBatch]:
    """Apply expert-supplied labels on top of *prior_labels*.

    Returns ``(scores_used, labels_used, FeedbackBatch)`` containing only the
    records for which a binary expert label is available — those are the
    records you want to feed into the calibration metric.
    """
    n = len(record_ids)
    if not (n == len(scores) == len(prior_labels)):
        raise ValueError("record_ids, scores and prior_labels must have equal length")

    by_rid: dict[str, int] = {}
    for it in expert_items:
        coerced = _coerce_label(it.label)
        if coerced is None:
            continue
        by_rid[it.record_id] = coerced

    out_scores: list[float] = []
    out_labels: list[int] = []
    relabelled = 0
    for rid, s, prev in zip(record_ids, scores, prior_labels, strict=True):
        new = by_rid.get(rid)
        if new is None:
            continue  # no expert label → skip
        if prev != new:
            relabelled += 1
        out_scores.append(float(s))
        out_labels.append(int(new))

    if not out_labels:
        raise ValueError(
            "no expert labels matched the supplied record IDs — nothing to "
            "feed back into calibration"
        )

    batch = FeedbackBatch(n_total=n, n_relabelled=relabelled,
                          n_used=len(out_labels))
    return out_scores, out_labels, batch


def recompute_ece(record_ids: Sequence[str],
                  scores: Sequence[float],
                  prior_labels: Sequence[int | None],
                  expert_items: Iterable[QueueItem],
                  *, n_bins: int = 10) -> tuple[CalibrationResult, FeedbackBatch]:
    """Recompute Expected Calibration Error using only expert-validated labels."""
    s, y, batch = merge_labels(record_ids, scores, prior_labels, expert_items)
    res = expected_calibration_error(s, y, n_bins=n_bins)
    res.extra = dict(res.extra)
    res.extra["feedback"] = batch.as_dict()
    return res, batch


__all__ = [
    "FeedbackBatch",
    "merge_labels",
    "recompute_ece",
]
