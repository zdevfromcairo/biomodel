"""Counterfactual ("what-if") drift simulation.

Given a current batch and a baseline, recompute drift after dropping records
matching a set of ``{dimension: [values_to_exclude]}`` filters. This lets an
operator test hypotheses like *"does the alert disappear if I exclude
ScannerY?"* before paying the cost of pulling that scanner offline.

Returns a serialisable dict describing pre/post severity for each metric.
"""

from __future__ import annotations

from typing import Any

from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.metrics.drift import js_divergence_categorical, ks_test, psi
from biomodel_monitor.schema.models import PredictionBatch, PredictionRecord


def _filter_records(
    records: list[PredictionRecord],
    exclude: dict[str, list[str]] | None,
) -> list[PredictionRecord]:
    if not exclude:
        return list(records)
    out: list[PredictionRecord] = []
    for r in records:
        keep = True
        for dim, values in exclude.items():
            v = getattr(r, dim, None)
            if v is not None and str(v) in {str(x) for x in values}:
                keep = False
                break
        if keep:
            out.append(r)
    return out


def _output_drift(scores_before: list[float], scores_after: list[float]) -> dict[str, Any]:
    psi_b = psi(scores_before, scores_after)
    ks_b = ks_test(scores_before, scores_after)
    return {
        "psi": psi_b.as_dict(),
        "ks": ks_b.as_dict(),
    }


def counterfactual_drift(
    batch: PredictionBatch,
    baseline: Baseline | None,
    *,
    exclude: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Recompute drift on ``batch`` minus the excluded slices."""
    base_records = list(batch.records)
    cf_records = _filter_records(base_records, exclude)
    n_dropped = len(base_records) - len(cf_records)

    base_scores = [r.score for r in base_records]
    cf_scores = [r.score for r in cf_records]

    result: dict[str, Any] = {
        "exclude": exclude or {},
        "n_records_before": len(base_records),
        "n_records_after": len(cf_records),
        "n_dropped": n_dropped,
        "output_drift": {
            "before": None,
            "after": None,
        },
        "modality_drift": [],
    }

    if baseline is not None and baseline.scores:
        result["output_drift"]["before"] = _output_drift(baseline.scores, base_scores)
        if cf_scores:
            result["output_drift"]["after"] = _output_drift(baseline.scores, cf_scores)
        for dim_name, ref_vals in (
            ("site_id", baseline.sites),
            ("scanner_id", baseline.scanners),
            ("stain", baseline.stains),
            ("tissue_type", baseline.tissue_types),
        ):
            ref_clean = [x for x in (ref_vals or []) if x != ""]
            if not ref_clean:
                continue
            before_vals = [getattr(r, dim_name) or "" for r in base_records]
            after_vals = [getattr(r, dim_name) or "" for r in cf_records]
            before_clean = [x for x in before_vals if x != ""]
            after_clean = [x for x in after_vals if x != ""]
            if not before_clean or not after_clean:
                continue
            result["modality_drift"].append({
                "dimension": dim_name,
                "before": js_divergence_categorical(ref_clean, before_clean).as_dict(),
                "after": js_divergence_categorical(ref_clean, after_clean).as_dict(),
            })

    return result


__all__ = ["counterfactual_drift"]
