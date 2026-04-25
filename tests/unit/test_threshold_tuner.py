"""Tests for the threshold tuner."""

from __future__ import annotations

from biomodel_monitor.alerts.threshold_tuner import propose_thresholds
from biomodel_monitor.incidents.workspace import IncidentWorkspace
from biomodel_monitor.store.repository import AlertRecord, MetricsStore


def _seed_alert(store, *, key, score, category="drift"):
    run = store.create_run(batch_id="b", model_id="m", model_version="1")
    store.insert_alerts([
        AlertRecord(run_id=run.run_id, batch_id="b", model_id="m",
                    model_version="1", key=key, title="t",
                    severity="warn", score=score, category=category),
    ])


def test_proposal_separates_tp_from_fp(tmp_path):
    store = MetricsStore(tmp_path / "s.db")
    ws = IncidentWorkspace(store)
    # 10 fps with low scores
    for i, sc in enumerate([1.0, 1.5, 2.0, 1.2, 1.8, 1.4, 1.6, 1.9, 1.1, 1.7]):
        _seed_alert(store, key=f"fp{i}", score=sc)
        ws.label(model_id="m", model_version="1", alert_key=f"fp{i}", label="fp")
    # 10 tps with high scores
    for i, sc in enumerate([6.0, 5.5, 7.2, 6.8, 6.1, 7.0, 5.9, 6.5, 6.4, 6.7]):
        _seed_alert(store, key=f"tp{i}", score=sc)
        ws.label(model_id="m", model_version="1", alert_key=f"tp{i}", label="tp")

    proposals = propose_thresholds(
        store, model_id="m", model_version="1", target_recall=0.9,
    )
    assert len(proposals) == 1
    p = proposals[0]
    assert p.category == "drift"
    assert p.n_tp == 10 and p.n_fp == 10
    # threshold should sit between the FP and TP score clouds
    assert 2.5 < p.proposed_min_score < 6.0
    assert p.expected_tpr >= 0.9
    assert p.expected_fpr <= 0.2


def test_insufficient_labels_keeps_threshold(tmp_path):
    store = MetricsStore(tmp_path / "s.db")
    ws = IncidentWorkspace(store)
    _seed_alert(store, key="k1", score=1.0)
    ws.label(model_id="m", model_version="1", alert_key="k1", label="fp")
    proposals = propose_thresholds(store, model_id="m", model_version="1")
    assert proposals[0].note.startswith("insufficient")
