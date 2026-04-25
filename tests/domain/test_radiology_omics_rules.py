"""Domain tests for radiology + omics rule packs."""

from __future__ import annotations

from datetime import datetime, timezone

from biomodel_monitor.ingest.loader import records_to_batch
from biomodel_monitor.metrics.domain_rules import (
    builtin_omics_rules,
    builtin_radiology_rules,
)


def _record(i: int, *, score: float, features: dict, extra: dict | None = None,
            tissue_type: str | None = None):
    return {
        "prediction_id": f"r{i}",
        "model_id": "m1",
        "model_version": "1.0.0",
        "timestamp": datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat(),
        "site_id": "S1",
        "tissue_type": tissue_type,
        "prediction": int(score >= 0.5),
        "score": score,
        "features": features,
        "extra": extra or {},
    }


# ---------- radiology ----------

def test_laterality_consistency_flags_opposite_side_driver():
    records = [
        _record(0, score=0.9, features={"rt_signal": 0.85, "lt_signal": 0.05},
                extra={"laterality": "left"}),
        _record(1, score=0.9, features={"lt_signal": 0.85, "rt_signal": 0.05},
                extra={"laterality": "left"}),  # ok
    ]
    batch = records_to_batch(records, batch_id="b")
    reg = builtin_radiology_rules()
    violations = [v for v in reg.run(batch.records) if v.rule == "laterality_consistency"]
    assert {v.record_id for v in violations} == {"r0"}


def test_anatomy_prior_flags_low_fov():
    records = [
        _record(0, score=0.9, features={"region_in_fov": 0.05}),
        _record(1, score=0.9, features={"region_in_fov": 0.9}),  # ok
        _record(2, score=0.3, features={"region_in_fov": 0.05}),  # low score → ok
    ]
    batch = records_to_batch(records, batch_id="b")
    reg = builtin_radiology_rules()
    bad = [v for v in reg.run(batch.records) if v.rule == "anatomy_prior"]
    assert {v.record_id for v in bad} == {"r0"}


def test_modality_cross_check_flags_disagreement():
    records = [
        _record(0, score=0.9, features={"modality_secondary_score": 0.05}),
        _record(1, score=0.9, features={"modality_secondary_score": 0.85}),
    ]
    batch = records_to_batch(records, batch_id="b")
    reg = builtin_radiology_rules()
    bad = [v for v in reg.run(batch.records) if v.rule == "modality_cross_check"]
    assert {v.record_id for v in bad} == {"r0"}


# ---------- omics ----------

def test_expression_bounds_flags_negative_tpm():
    records = [
        _record(0, score=0.5, features={"gene_a_tpm": -2.0}),
        _record(1, score=0.5, features={"gene_a_tpm": 100.0}),
    ]
    batch = records_to_batch(records, batch_id="b")
    reg = builtin_omics_rules()
    bad = [v for v in reg.run(batch.records) if v.rule == "expression_bounds"]
    assert {v.record_id for v in bad} == {"r0"}


def test_pathway_consistency_detects_sign_violation():
    records = [
        _record(0, score=0.5, features={},
                extra={"pathway_pairs": [[2.0, -1.5, 1]]}),  # +ve sign but opposite
        _record(1, score=0.5, features={},
                extra={"pathway_pairs": [[2.0, 1.5, 1]]}),  # ok
    ]
    batch = records_to_batch(records, batch_id="b")
    reg = builtin_omics_rules()
    bad = [v for v in reg.run(batch.records) if v.rule == "pathway_consistency"]
    assert {v.record_id for v in bad} == {"r0"}
