"""Integration test for the persistent metrics store + persistence-aware severity."""

from __future__ import annotations

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.pipeline import run_pipeline
from biomodel_monitor.store.repository import MetricsStore


def test_pipeline_persists_runs_and_alerts(tmp_path, make_batch):
    store = MetricsStore(tmp_path / "s.db")
    ref = make_batch(n=400, seed=1, score_bias=0.0)
    baseline = Baseline.from_batch(ref)
    cur = make_batch(n=400, seed=2, score_bias=0.4)  # injects score drift
    cfg = AlertConfig(min_severity="warn")

    res1 = run_pipeline(cur, baseline=baseline, alert_config=cfg, store=store)
    assert res1.run_id is not None
    assert res1.alerts, "expected at least one alert from injected drift"
    persisted = store.list_alerts(model_id=cur.metadata.model_id,
                                  model_version=cur.metadata.model_version)
    assert len(persisted) == len(res1.alerts)

    # second run with the same drift → persistence increases for repeating keys
    res2 = run_pipeline(cur, baseline=baseline, alert_config=cfg, store=store)
    repeating = set(a.key for a in res1.alerts) & set(a.key for a in res2.alerts)
    assert repeating, "at least one alert should repeat"
    for k in repeating:
        assert res2.persistence_by_key[k] >= 2

    runs = store.list_runs(model_id=cur.metadata.model_id,
                           model_version=cur.metadata.model_version)
    assert len(runs) == 2


def test_pipeline_uses_promoted_baseline_when_present(tmp_path, make_batch):
    store = MetricsStore(tmp_path / "s.db")
    ref = make_batch(n=300, seed=1)
    payload = {
        "model_id": ref.metadata.model_id,
        "model_version": ref.metadata.model_version,
        "cohort": None, "n": len(ref.records),
        "scores": [r.score for r in ref.records],
        "features": {}, "sites": [r.site_id for r in ref.records],
        "scanners": [r.scanner_id or "" for r in ref.records],
        "stains": [r.stain or "" for r in ref.records],
        "tissue_types": [r.tissue_type or "" for r in ref.records],
    }
    bid = store.save_baseline(
        model_id=ref.metadata.model_id, model_version=ref.metadata.model_version,
        cohort=None, site_id=None, status="candidate", payload=payload,
    )
    store.promote_baseline(bid)
    p = store.get_promoted_baseline(
        model_id=ref.metadata.model_id, model_version=ref.metadata.model_version,
    )
    assert p is not None
    # smoke run that exercises store integration end-to-end
    cur = make_batch(n=100, seed=2, score_bias=0.4)
    res = run_pipeline(cur, store=store)
    assert res.run_id
