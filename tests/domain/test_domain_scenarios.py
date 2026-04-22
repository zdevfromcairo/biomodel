"""Domain-specific tests: synthetic biological scenarios."""

import random
from datetime import datetime, timezone

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.ingest.loader import records_to_batch
from biomodel_monitor.metrics.plausibility import builtin_pathology_rules
from biomodel_monitor.pipeline import run_pipeline


def _row(i, **over):
    base = {
        "prediction_id": f"p{i}",
        "model_id": "m",
        "model_version": "1.0",
        "timestamp": datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat(),
        "site_id": "A",
        "scanner_id": "S1",
        "stain": "H&E",
        "tissue_type": "breast",
        "cohort": "c1",
        "prediction": 1,
        "score": 0.8,
        "ground_truth": 1,
        "features": {"f1": 0.0},
        "extra": {},
    }
    base.update(over)
    return base


def test_implausible_tumor_vs_tissue_area_flagged():
    rows = [
        _row(i, score=0.9, extra={"tissue_area_fraction": 0.05})
        for i in range(20)
    ]
    batch = records_to_batch(rows, batch_id="b")
    reg = builtin_pathology_rules()
    violations = reg.run(batch.records)
    assert any(v.rule == "tumor_probability_vs_tissue_area" for v in violations)


def test_mitosis_density_implausible_flagged():
    rows = [
        _row(i, extra={"mitosis_count": 1000, "field_area_mm2": 1.0})
        for i in range(5)
    ]
    batch = records_to_batch(rows, batch_id="b")
    reg = builtin_pathology_rules()
    violations = reg.run(batch.records)
    assert any(v.rule == "mitosis_count_vs_field_area" for v in violations)


def test_mutually_exclusive_markers_flagged():
    rows = [
        _row(
            i,
            extra={
                "markers": {"ER": 0.9, "TripleNegative": 0.9},
                "exclusive_marker_pairs": [["ER", "TripleNegative"]],
            },
        )
        for i in range(3)
    ]
    batch = records_to_batch(rows, batch_id="b")
    reg = builtin_pathology_rules()
    violations = reg.run(batch.records)
    assert any(v.rule == "mutually_exclusive_markers" for v in violations)


def test_slide_tile_inconsistency_flagged():
    rows = [
        _row(i, score=0.9, extra={"tile_scores": [0.1] * 10}) for i in range(3)
    ]
    batch = records_to_batch(rows, batch_id="b")
    reg = builtin_pathology_rules()
    violations = reg.run(batch.records)
    assert any(v.rule == "slide_tile_consistency" for v in violations)


def test_clean_batch_yields_no_plausibility_violations():
    rows = [_row(i, score=0.4, extra={"tissue_area_fraction": 0.6, "tile_scores": [0.4] * 8}) for i in range(20)]
    batch = records_to_batch(rows, batch_id="b")
    reg = builtin_pathology_rules()
    violations = reg.run(batch.records)
    assert violations == []


def test_staining_protocol_shift_detected():
    """Modality drift: stain mix changes between reference and current."""
    rng = random.Random(0)
    ref_rows = [
        _row(i, stain=rng.choice(["H&E", "H&E", "IHC"])) for i in range(300)
    ]
    cur_rows = [
        _row(i, stain="IHC") for i in range(300)
    ]
    ref = records_to_batch(ref_rows, batch_id="r")
    cur = records_to_batch(cur_rows, batch_id="c")
    baseline = Baseline.from_batch(ref)
    result = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))
    stain_drift = [
        a for a in result.alerts
        if a.category == "drift" and "stain" in a.details.get("kind", "")
    ]
    assert stain_drift, "expected stain modality drift to be flagged"


def test_class_imbalance_shift_via_score_drift():
    """A large class-prevalence shift shows up as output-score drift."""
    rng = random.Random(0)
    ref_rows = [
        _row(i, score=rng.betavariate(2, 5), ground_truth=None) for i in range(400)
    ]
    cur_rows = [
        _row(i, score=rng.betavariate(5, 2), ground_truth=None) for i in range(400)
    ]
    ref = records_to_batch(ref_rows, batch_id="r")
    cur = records_to_batch(cur_rows, batch_id="c")
    baseline = Baseline.from_batch(ref)
    result = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))
    out = [a for a in result.alerts if a.category == "drift" and "output_score" in a.details.get("kind", "")]
    assert out, "expected output_score drift alert under prevalence shift"


def test_subgroup_underperformance_flagged():
    rng = random.Random(0)
    rows = []
    # site A: well calibrated
    for i in range(200):
        s = rng.betavariate(5, 2)
        y = 1 if rng.random() < s else 0
        rows.append(_row(i, site_id="A", score=s, ground_truth=y))
    # site B: predictions inverted (worst-case)
    for i in range(200, 400):
        s = rng.betavariate(5, 2)
        y = 1 if rng.random() < (1 - s) else 0
        rows.append(_row(i, site_id="B", score=s, ground_truth=y))
    batch = records_to_batch(rows, batch_id="b")
    result = run_pipeline(batch, alert_config=AlertConfig(min_severity="warn"))
    sub = [a for a in result.alerts if a.category == "subgroup" and "site_id=B" in (a.root_cause_hint or "")]
    assert sub, "expected subgroup degradation alert for site B"
