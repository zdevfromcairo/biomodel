"""Unit tests for the SQLite metrics store."""

from __future__ import annotations

from biomodel_monitor.store.repository import (
    AlertRecord,
    Annotation,
    BatchRecord,
    MetricsStore,
)


def test_store_round_trip(tmp_path):
    s = MetricsStore(tmp_path / "store.db")
    s.upsert_batch(
        BatchRecord(batch_id="b1", model_id="m", model_version="1",
                    n_records=10, created_at="2026-04-22T00:00:00+00:00")
    )
    run = s.create_run(batch_id="b1", model_id="m", model_version="1")
    s.insert_alerts([
        AlertRecord(run_id=run.run_id, batch_id="b1", model_id="m", model_version="1",
                    key="k1", title="t", severity="alert", score=4.0, category="drift"),
    ])
    s.set_run_alerts(run.run_id, 1)

    runs = s.list_runs(model_id="m", model_version="1")
    assert len(runs) == 1
    assert runs[0].n_alerts == 1
    alerts = s.list_alerts(model_id="m", model_version="1")
    assert len(alerts) == 1 and alerts[0].key == "k1"


def test_alert_persistence(tmp_path):
    s = MetricsStore(tmp_path / "store.db")
    for i in range(3):
        run = s.create_run(batch_id=f"b{i}", model_id="m", model_version="1")
        s.insert_alerts([
            AlertRecord(run_id=run.run_id, batch_id=f"b{i}", model_id="m",
                        model_version="1", key="recurring", title="t",
                        severity="warn", score=2.0, category="drift"),
        ])
    # one-off alert in the latest run only
    run = s.create_run(batch_id="b3", model_id="m", model_version="1")
    s.insert_alerts([
        AlertRecord(run_id=run.run_id, batch_id="b3", model_id="m",
                    model_version="1", key="oneoff", title="t",
                    severity="warn", score=2.0, category="drift"),
    ])
    assert s.alert_persistence(model_id="m", model_version="1", key="recurring") >= 3
    assert s.alert_persistence(model_id="m", model_version="1", key="oneoff") == 1


def test_annotations_and_status(tmp_path):
    s = MetricsStore(tmp_path / "store.db")
    s.add_annotation(Annotation(alert_key="k", model_id="m", model_version="1",
                                kind="ack", actor="alice"))
    assert s.alert_status(model_id="m", model_version="1", key="k") == "acknowledged"
    s.add_annotation(Annotation(alert_key="k", model_id="m", model_version="1",
                                kind="label", label="fp", actor="alice"))
    assert s.alert_label(model_id="m", model_version="1", key="k") == "fp"
    s.add_annotation(Annotation(alert_key="k", model_id="m", model_version="1",
                                kind="resolve", actor="bob"))
    assert s.alert_status(model_id="m", model_version="1", key="k") == "resolved"


def test_baseline_promotion(tmp_path):
    s = MetricsStore(tmp_path / "store.db")
    bid_a = s.save_baseline(
        model_id="m", model_version="1", cohort=None, site_id=None,
        status="candidate", payload={"n": 100, "scores": [0.1, 0.2]},
    )
    bid_b = s.save_baseline(
        model_id="m", model_version="1", cohort=None, site_id=None,
        status="candidate", payload={"n": 200, "scores": [0.3]},
    )
    s.promote_baseline(bid_b)
    p = s.get_promoted_baseline(model_id="m", model_version="1")
    assert p is not None and p["payload"]["n"] == 200
    # earlier promotion of bid_a, then promote bid_b should retire bid_a
    s.promote_baseline(bid_a)
    p2 = s.get_promoted_baseline(model_id="m", model_version="1")
    assert p2 is not None and p2["payload"]["n"] == 100
    only_promoted = [b for b in s.list_baselines(model_id="m", model_version="1")
                     if b["status"] == "promoted"]
    assert len(only_promoted) == 1
