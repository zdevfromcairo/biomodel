"""Tests for the v0.8 concurrent pipeline."""

from __future__ import annotations

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.pipeline import run_pipeline
from biomodel_monitor.pipeline_async import run_pipeline_async


def test_async_pipeline_matches_sync_alert_keys(make_batch):
    ref = make_batch(n=300, seed=0)
    cur = make_batch(n=300, seed=2, score_bias=0.4)
    baseline = Baseline.from_batch(ref)

    sync_result = run_pipeline(
        cur, baseline=baseline,
        alert_config=AlertConfig(min_severity="warn"),
    )
    async_result, stats = run_pipeline_async(
        cur, baseline=baseline,
        alert_config=AlertConfig(min_severity="warn"),
        workers=4,
    )
    sync_keys = sorted(a.key for a in sync_result.alerts)
    async_keys = sorted(a.key for a in async_result.alerts)
    assert sync_keys == async_keys
    # Per-row sets identical (NaN-bearing extras compared via JSON round-trip).
    import json
    def _json_norm(rows):
        return json.loads(json.dumps(rows, default=str))
    assert _json_norm(sync_result.drift_results) == _json_norm(async_result.drift_results)
    assert _json_norm(sync_result.calibration_results) == _json_norm(
        async_result.calibration_results
    )
    assert _json_norm(sync_result.silent_failure_results) == _json_norm(
        async_result.silent_failure_results
    )
    # Stats are populated.
    assert stats.total_ms >= 0
    assert stats.workers == 4
    assert stats.parallel is True


def test_async_pipeline_single_worker_still_works(make_batch):
    cur = make_batch(n=80, seed=1)
    res, stats = run_pipeline_async(cur, workers=1)
    assert stats.workers == 1
    assert stats.parallel is False
    assert isinstance(res.alerts, list)
