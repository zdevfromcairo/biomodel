"""Tests for v0.5 intelligence modules."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from biomodel_monitor.intelligence.anomaly import robust_zscore
from biomodel_monitor.intelligence.attribution import attribute_alert
from biomodel_monitor.intelligence.changepoint import detect_changepoints
from biomodel_monitor.intelligence.whatif import counterfactual_drift
from biomodel_monitor.schema.models import (
    BatchMetadata,
    PredictionBatch,
    PredictionRecord,
)


def _record(i: int, *, score: float, **kw) -> PredictionRecord:
    base = dict(
        prediction_id=f"p{i}", model_id="m", model_version="v1",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        site_id=kw.pop("site_id", "S1"),
        prediction=int(score >= 0.5), score=score,
    )
    base.update(kw)
    return PredictionRecord(**base)


def _batch(records):
    return PredictionBatch(
        metadata=BatchMetadata(
            batch_id="b1", model_id="m", model_version="v1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        records=records,
    )


# --- changepoint -------------------------------------------------------------
def test_changepoint_detects_step_function():
    series = [0.1] * 30 + [0.9] * 30
    res = detect_changepoints(series, min_size=5, threshold=0.5)
    assert res.indices, "expected at least one changepoint"
    # accepted changepoint should be near the true break (index 30)
    assert min(abs(i - 30) for i in res.indices) <= 3
    assert len(res.segments) == len(res.indices) + 1
    # segment means should differ substantially
    means = [s["mean"] for s in res.segments]
    assert max(means) - min(means) > 0.5


def test_changepoint_returns_empty_for_flat():
    res = detect_changepoints([0.5] * 50)
    assert res.indices == []
    assert len(res.segments) == 1


def test_changepoint_handles_short_series():
    res = detect_changepoints([0.1, 0.2, 0.9])  # below 2*min_size
    assert res.indices == []


def test_changepoint_respects_max_changepoints():
    # alternating step series → many candidates; cap to 2
    series = ([0.0] * 10 + [1.0] * 10) * 5
    res = detect_changepoints(series, min_size=5, threshold=0.5, max_changepoints=2)
    assert len(res.indices) <= 2


# --- anomaly -----------------------------------------------------------------
def test_robust_zscore_flags_outlier():
    history = [0.5] * 20
    res = robust_zscore([*history, 5.0], warn_threshold=3.0, alert_threshold=5.0)
    assert res.severity == "alert"
    assert res.score > 5.0


def test_robust_zscore_silent_on_steady_state():
    res = robust_zscore([0.5, 0.51, 0.49, 0.5, 0.5, 0.51])
    assert res.severity == "ok"
    assert res.score < 3.0


def test_robust_zscore_short_series_returns_zero():
    res = robust_zscore([0.5])
    assert res.score == 0.0
    assert res.severity == "ok"


# --- whatif ------------------------------------------------------------------
def _batch_records_drift_a_b():
    return [_record(i, score=0.9, scanner_id="X") for i in range(10)] + [
        _record(100 + i, score=0.1, scanner_id="Y") for i in range(10)
    ]


def test_counterfactual_drift_drops_excluded_records():
    records = _batch_records_drift_a_b()
    out = counterfactual_drift(_batch(records), baseline=None,
                               exclude={"scanner_id": ["Y"]})
    assert out["n_records_before"] == 20
    assert out["n_records_after"] == 10
    assert out["n_dropped"] == 10


def test_counterfactual_drift_recomputes_output_drift():
    from biomodel_monitor.baselines.store import Baseline
    records = [
        _record(i, score=0.4, scanner_id="X") for i in range(50)
    ] + [
        _record(100 + i, score=0.95, scanner_id="Y") for i in range(50)
    ]
    baseline = Baseline(
        model_id="m", model_version="v1", cohort=None, n=100,
        scores=[0.4] * 100,
    )
    out = counterfactual_drift(_batch(records), baseline,
                               exclude={"scanner_id": ["Y"]})
    before_psi = out["output_drift"]["before"]["psi"]["value"]
    after_psi = out["output_drift"]["after"]["psi"]["value"]
    # excluding the drifted scanner should LOWER PSI
    assert after_psi < before_psi


# --- attribution -------------------------------------------------------------
def test_attribute_alert_ranks_top_contributor():
    # 80 records mostly from site A with score ~0.5; 20 from site B at 0.95
    records = [
        _record(i, score=0.5, site_id="A") for i in range(80)
    ] + [
        _record(100 + i, score=0.95, site_id="B") for i in range(20)
    ]
    attr = attribute_alert("output_drift", records, top_k=3)
    # site B has bigger delta from pooled mean → should appear in top contributors
    assert any(c.dimension == "site_id" and c.value == "B" for c in attr.contributors)


def test_attribute_alert_respects_min_n():
    records = [
        _record(i, score=0.5, site_id="A") for i in range(50)
    ] + [
        _record(100 + i, score=0.99, site_id="B") for i in range(2)
    ]
    attr = attribute_alert("k", records, min_n=10)
    assert all(c.value != "B" for c in attr.contributors)


# --- model card --------------------------------------------------------------
def test_build_model_card_runs_against_empty_store(tmp_path):
    from biomodel_monitor.intelligence.modelcard import build_model_card
    from biomodel_monitor.store.repository import MetricsStore
    store = MetricsStore(str(tmp_path / "m.db"))
    try:
        card = build_model_card(store, model_id="m", model_version="v1")
    finally:
        store.close()
    assert "Model Card" in card and "v1" in card and "Disclaimer" in card


# --- active learning ---------------------------------------------------------
def test_rank_incidents_for_review_orders_by_information_gain(tmp_path):
    from biomodel_monitor.incidents.workspace import IncidentWorkspace
    from biomodel_monitor.intelligence.active_learning import rank_incidents_for_review
    from biomodel_monitor.store.repository import (
        AlertRecord,
        BatchRecord,
        MetricsStore,
    )

    store = MetricsStore(str(tmp_path / "m.db"))
    try:
        store.upsert_batch(BatchRecord(
            batch_id="b1", model_id="m", model_version="v1",
            n_records=1, created_at="2024-01-01T00:00:00",
        ))
        run = store.create_run(batch_id="b1", model_id="m", model_version="v1")
        store.insert_alerts([
            AlertRecord(
                run_id=run.run_id, batch_id="b1", model_id="m", model_version="v1",
                key=f"k{i}", title=f"t{i}", severity="alert", score=4.0,
                category="drift", root_cause_hint=None, persistence=1,
                details_json="{}", created_at="2024-01-01T00:00:00",
            ) for i in range(3)
        ])
        # label k0 as fp so the inferred TP-rate for category 'drift' is < 0.5
        ws = IncidentWorkspace(store)
        ws.label(model_id="m", model_version="v1", alert_key="k0", label="fp")
        proposals = rank_incidents_for_review(
            store, model_id="m", model_version="v1", top_k=10,
        )
        # k0 is labeled, so excluded
        keys = [p.alert_key for p in proposals]
        assert "k0" not in keys
        assert {"k1", "k2"}.issubset(set(keys))
    finally:
        store.close()


# Sanity: smoke import path
def test_intelligence_package_exports():
    import biomodel_monitor.intelligence as I  # noqa: N812
    assert hasattr(I, "detect_changepoints")
    assert hasattr(I, "robust_zscore")
    assert hasattr(I, "build_model_card")
    assert hasattr(I, "counterfactual_drift")
    assert hasattr(I, "attribute_alert")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
