"""End-to-end batch monitoring pipeline.

Pulls the metrics + alerts + report engines together so a vendor only needs
to call :func:`run_pipeline` (or invoke the CLI).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from biomodel_monitor import __version__
from biomodel_monitor.alerts.engine import Alert, AlertConfig, AlertEngine
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.metrics.calibration import (
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
)
from biomodel_monitor.metrics.drift import (
    js_divergence_categorical,
    ks_test,
    psi,
)
from biomodel_monitor.metrics.plausibility import RuleRegistry, builtin_pathology_rules
from biomodel_monitor.metrics.silent_failure import (
    confidence_accuracy_decoupling,
    entropy_collapse,
    prediction_drift_without_input_drift,
)
from biomodel_monitor.metrics.subgroup import slice_metrics
from biomodel_monitor.reports.render import render_report
from biomodel_monitor.schema.models import PredictionBatch

DEFAULT_DIMENSIONS = ("site_id", "scanner_id", "stain", "tissue_type", "cohort")


@dataclass
class PipelineResult:
    drift_results: list[dict] = field(default_factory=list)
    calibration_results: list[dict] = field(default_factory=list)
    subgroup_results: list[dict] = field(default_factory=list)
    plausibility_violations: list[dict] = field(default_factory=list)
    silent_failure_results: list[dict] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "drift_results": self.drift_results,
            "calibration_results": self.calibration_results,
            "subgroup_results": self.subgroup_results,
            "plausibility_violations": self.plausibility_violations,
            "silent_failure_results": self.silent_failure_results,
            "alerts": [a.as_dict() for a in self.alerts],
        }


def _compute_drift(
    batch: PredictionBatch, baseline: Baseline | None
) -> tuple[list[dict], list[str], str]:
    drift_rows: list[dict] = []
    input_severities: list[str] = []
    output_severity = "ok"
    if baseline is None:
        return drift_rows, input_severities, output_severity

    cur_scores = [r.score for r in batch.records]
    if baseline.scores and cur_scores:
        out = psi(baseline.scores, cur_scores)
        output_severity = out.severity
        drift_rows.append({"kind": "output_score", **out.as_dict()})
        ks = ks_test(baseline.scores, cur_scores)
        drift_rows.append({"kind": "output_score", **ks.as_dict()})
        if ks.severity == "alert":
            output_severity = "alert"
        elif ks.severity == "warn" and output_severity == "ok":
            output_severity = "warn"

    # input feature drift
    cur_features: dict[str, list[float]] = {}
    for r in batch.records:
        if r.features:
            for k, v in r.features.items():
                cur_features.setdefault(k, []).append(float(v))
    for fname, ref_vals in baseline.features.items():
        cur_vals = cur_features.get(fname)
        if not cur_vals or not ref_vals:
            continue
        d = psi(ref_vals, cur_vals)
        drift_rows.append({"kind": f"input_feature:{fname}", **d.as_dict()})
        input_severities.append(d.severity)

    # modality drift
    for dim_name, ref_vals in (
        ("site_id", baseline.sites),
        ("scanner_id", baseline.scanners),
        ("stain", baseline.stains),
        ("tissue_type", baseline.tissue_types),
    ):
        cur_vals = [getattr(r, dim_name) or "" for r in batch.records]
        ref_clean = [x for x in ref_vals if x != ""]
        cur_clean = [x for x in cur_vals if x != ""]
        if not ref_clean or not cur_clean:
            continue
        d = js_divergence_categorical(ref_clean, cur_clean)
        d.name = f"js_divergence:{dim_name}"
        drift_rows.append({"kind": f"modality:{dim_name}", **d.as_dict()})
        input_severities.append(d.severity)

    return drift_rows, input_severities, output_severity


def _compute_calibration(batch: PredictionBatch) -> list[dict]:
    rows: list[dict] = []
    scores: list[float] = []
    labels: list[int] = []
    for r in batch.records:
        if r.ground_truth is None:
            continue
        try:
            y = int(r.ground_truth)
        except (TypeError, ValueError):
            continue
        if y not in (0, 1):
            continue
        scores.append(float(r.score))
        labels.append(y)
    if not scores:
        return rows
    rows.append({**expected_calibration_error(scores, labels).as_dict()})
    rows.append({**maximum_calibration_error(scores, labels).as_dict()})
    rows.append({**brier_score(scores, labels).as_dict()})
    # per-cohort ECE
    by_cohort: dict[str, tuple[list[float], list[int]]] = {}
    for r, y in zip(
        [rec for rec in batch.records if rec.ground_truth is not None and str(rec.ground_truth) in ("0", "1", "True", "False")],
        labels,
    ):
        c = r.cohort or "_global"
        by_cohort.setdefault(c, ([], []))
        by_cohort[c][0].append(float(r.score))
        by_cohort[c][1].append(int(y))
    for cohort, (s, y) in by_cohort.items():
        if len(s) >= 30:
            res = expected_calibration_error(s, y)
            res.name = f"ece:cohort={cohort}"
            rows.append(res.as_dict())
    return rows


def _compute_subgroups(
    batch: PredictionBatch,
    *,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    threshold: float = 0.5,
    min_n: int = 30,
) -> list[dict]:
    has_gt = any(r.ground_truth is not None for r in batch.records)
    scores = [r.score for r in batch.records]
    if has_gt:
        labels: list[int] = []
        keep_mask: list[bool] = []
        for r in batch.records:
            try:
                y = int(r.ground_truth) if r.ground_truth is not None else None
            except (TypeError, ValueError):
                y = None
            keep_mask.append(y in (0, 1))
            if y in (0, 1):
                labels.append(int(y))
        scores_used = [s for s, k in zip(scores, keep_mask) if k]
        out: list[dict] = []
        for d in dimensions:
            groups = [getattr(r, d) for r, k in zip(batch.records, keep_mask) if k]
            if not groups:
                continue
            results = slice_metrics(
                dimension=d,
                groups=groups,
                scores=scores_used,
                labels=labels,
                threshold=threshold,
                min_n=min_n,
                metric="accuracy",
            )
            out.extend([r.as_dict() for r in results])
        return out
    # no ground truth: report mean_score by slice
    out2: list[dict] = []
    for d in dimensions:
        groups = [getattr(r, d) for r in batch.records]
        if not groups:
            continue
        results = slice_metrics(
            dimension=d,
            groups=groups,
            scores=scores,
            min_n=min_n,
            metric="mean_score",
        )
        out2.extend([r.as_dict() for r in results])
    return out2


def _compute_silent_failure(
    batch: PredictionBatch,
    baseline: Baseline | None,
    drift_rows: list[dict],
    output_severity: str,
    input_severities: list[str],
) -> list[dict]:
    rows: list[dict] = []
    scores = [r.score for r in batch.records]
    if not scores:
        return rows
    ref_mean_h = None
    if baseline is not None and baseline.scores:
        # mean entropy of baseline for relative-drop comparison
        ref = np.asarray(baseline.scores, dtype=float)
        ref = np.clip(ref, 1e-12, 1 - 1e-12)
        ref_h = -(ref * np.log2(ref) + (1 - ref) * np.log2(1 - ref))
        ref_mean_h = float(ref_h.mean())
    rows.append(entropy_collapse(scores, reference_mean_entropy=ref_mean_h).as_dict())
    # confidence/accuracy decoupling — needs ground truth
    has_gt = any(r.ground_truth is not None for r in batch.records)
    if has_gt:
        s = []
        y = []
        for r in batch.records:
            try:
                yi = int(r.ground_truth) if r.ground_truth is not None else None
            except (TypeError, ValueError):
                yi = None
            if yi in (0, 1):
                s.append(float(r.score))
                y.append(int(yi))
        if s:
            rows.append(confidence_accuracy_decoupling(s, y).as_dict())
    rows.append(
        prediction_drift_without_input_drift(output_severity, input_severities).as_dict()
    )
    return rows


def run_pipeline(
    batch: PredictionBatch,
    *,
    baseline: Baseline | None = None,
    rule_registry: RuleRegistry | None = None,
    alert_config: AlertConfig | None = None,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    threshold: float = 0.5,
    min_subgroup_n: int = 30,
) -> PipelineResult:
    """Run the full Phase-1 monitoring pipeline on a batch."""
    rule_registry = rule_registry or builtin_pathology_rules()
    engine = AlertEngine(alert_config)

    drift_rows, input_sev, output_sev = _compute_drift(batch, baseline)
    calibration_rows = _compute_calibration(batch)
    subgroup_rows = _compute_subgroups(
        batch, dimensions=dimensions, threshold=threshold, min_n=min_subgroup_n
    )
    plausibility = rule_registry.run(batch.records)
    silent_rows = _compute_silent_failure(
        batch, baseline, drift_rows, output_sev, input_sev
    )

    # Convert dict rows back to lightweight objects for the alert engine.
    class _Bag:
        def __init__(self, d: dict) -> None:
            self.__dict__.update(d)

            def as_dict(_self=self, _d=d) -> dict:
                return _d

            self.as_dict = as_dict  # type: ignore[assignment]

    alerts: list[Alert] = []
    alerts += engine.from_drift([_Bag(r) for r in drift_rows], kind="batch")
    alerts += engine.from_calibration([_Bag(r) for r in calibration_rows])
    alerts += engine.from_subgroups([_Bag(r) for r in subgroup_rows])
    alerts += engine.from_plausibility([_Bag(v.as_dict()) for v in plausibility])
    alerts += engine.from_silent_failure([_Bag(r) for r in silent_rows])
    alerts = engine.deduplicate(alerts)

    return PipelineResult(
        drift_results=drift_rows,
        calibration_results=calibration_rows,
        subgroup_results=subgroup_rows,
        plausibility_violations=[v.as_dict() for v in plausibility],
        silent_failure_results=silent_rows,
        alerts=alerts,
    )


def write_outputs(
    batch: PredictionBatch,
    result: PipelineResult,
    *,
    out_dir: str | Path,
) -> dict[str, Path]:
    """Render Markdown + HTML reports and JSON metrics into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    context: dict[str, Any] = {
        "batch": batch,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": __version__,
        **result.as_dict(),
    }
    md = render_report(context, fmt="md")
    html = render_report(context, fmt="html")
    md_path = out / f"{batch.metadata.batch_id}.md"
    html_path = out / f"{batch.metadata.batch_id}.html"
    json_path = out / f"{batch.metadata.batch_id}.json"
    md_path.write_text(md)
    html_path.write_text(html)
    json_path.write_text(json.dumps(result.as_dict(), indent=2, default=str))
    return {"markdown": md_path, "html": html_path, "json": json_path}
