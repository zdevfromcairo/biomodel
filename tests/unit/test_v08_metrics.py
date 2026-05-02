"""Tests for v0.8 metric & intelligence modules."""

from __future__ import annotations

import numpy as np
import pytest

from biomodel_monitor.intelligence.drift_graph import build_drift_graph
from biomodel_monitor.intelligence.local_attribution import (
    aggregate_top_dimensions,
    attribute_local,
)
from biomodel_monitor.metrics.cusum import CUSUMMonitor, cusum_offline
from biomodel_monitor.metrics.embedding_drift import mmd_rbf
from biomodel_monitor.schema.models import PredictionRecord

# ---------------------------------------------------------------------------
# MMD
# ---------------------------------------------------------------------------

def test_mmd_null_distribution_p_value_is_uniform_ish():
    rng = np.random.default_rng(7)
    ref = rng.normal(0, 1, (150, 3))
    cur = rng.normal(0, 1, (150, 3))
    res = mmd_rbf(ref, cur, n_permutations=200, seed=7)
    assert res.severity == "ok"
    assert res.p_value is not None
    assert 0.05 <= res.p_value <= 0.95


def test_mmd_alt_detects_mean_shift():
    rng = np.random.default_rng(7)
    ref = rng.normal(0, 1, (200, 4))
    cur = rng.normal(0.7, 1, (200, 4))
    res = mmd_rbf(ref, cur, n_permutations=200, seed=7)
    assert res.severity in ("warn", "alert")
    assert res.p_value is not None and res.p_value < 0.05
    assert res.value > 0
    assert res.bandwidth and res.bandwidth > 0


def test_mmd_handles_1d_input():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 80)
    cur = rng.normal(2.0, 1, 80)
    res = mmd_rbf(list(ref), list(cur), n_permutations=50, seed=0)
    assert res.n_reference == 80 and res.n_current == 80
    assert res.severity in ("warn", "alert")


def test_mmd_dimension_mismatch_raises():
    with pytest.raises(ValueError):
        mmd_rbf(np.zeros((10, 3)), np.zeros((10, 5)))


def test_mmd_tiny_input_returns_ok():
    res = mmd_rbf([[0.0]], [[1.0]], n_permutations=10)
    assert res.severity == "ok"
    assert "insufficient samples" in res.extra.get("note", "")


# ---------------------------------------------------------------------------
# CUSUM
# ---------------------------------------------------------------------------

def test_cusum_no_shift_stays_ok():
    rng = np.random.default_rng(0)
    series = list(rng.normal(0, 1, 200))
    res = cusum_offline(series, target=0.0, sigma=1.0,
                        threshold=12.0, slack_k=1.0)
    assert res.severity == "ok"
    assert res.detected_at is None
    assert res.direction is None


def test_cusum_step_shift_is_detected():
    rng = np.random.default_rng(1)
    series = list(rng.normal(0, 1, 50)) + list(rng.normal(2.0, 1, 50))
    res = cusum_offline(series, target=0.0, sigma=1.0,
                        threshold=4.0, slack_k=0.5)
    assert res.severity == "alert"
    assert res.direction == "up"
    assert res.detected_at is not None
    # Shift starts at index 50; CUSUM should detect within ~10 steps.
    assert 50 <= res.detected_at <= 70


def test_cusum_downward_shift():
    rng = np.random.default_rng(2)
    series = list(rng.normal(0, 1, 50)) + list(rng.normal(-2.5, 1, 50))
    res = cusum_offline(series, target=0.0, sigma=1.0)
    assert res.direction == "down"


def test_cusum_monitor_reset_clears_detection():
    m = CUSUMMonitor(target=0.0, sigma=1.0, threshold=4.0)
    for _ in range(20):
        m.update(2.5)
    snap = m.snapshot()
    assert snap.severity == "alert"
    m.reset()
    assert m.snapshot().severity == "ok"
    assert m.snapshot().detected_at is None


def test_cusum_invalid_args():
    with pytest.raises(ValueError):
        CUSUMMonitor(target=0.0, sigma=0.0)
    with pytest.raises(ValueError):
        CUSUMMonitor(target=0.0, sigma=1.0, threshold=0.0)


def test_cusum_offline_estimates_target_when_omitted():
    rng = np.random.default_rng(3)
    series = list(rng.normal(5, 0.5, 30)) + list(rng.normal(7, 0.5, 30))
    res = cusum_offline(series)
    # Heuristic: warm up on the first ~third, so a 4σ-equivalent shift
    # later in the series is detectable.
    assert res.severity in ("warn", "alert")


# ---------------------------------------------------------------------------
# Local attribution
# ---------------------------------------------------------------------------

def _make_records(scores, *, sites=None, scanners=None):
    from datetime import datetime, timezone
    n = len(scores)
    sites = sites or ["A"] * n
    scanners = scanners or ["S1"] * n
    return [
        PredictionRecord(
            prediction_id=f"r{i}", model_id="m", model_version="v",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            site_id=sites[i], scanner_id=scanners[i],
            prediction=int(s >= 0.5), score=float(s),
        )
        for i, s in enumerate(scores)
    ]


def test_local_attribution_finds_outliers():
    # 100 ~0.5 records + 5 high outliers.
    scores = [0.5] * 100 + [0.99] * 5
    sites = ["main"] * 100 + ["bad"] * 5
    recs = _make_records(scores, sites=sites)
    res = attribute_local(recs, top_k=5)
    assert res.n_records == 105
    assert res.severity in ("warn", "alert")
    # Top-positive record should be one of the outliers.
    assert res.top_positive[0].score == pytest.approx(0.99)
    assert res.top_positive[0].site_id == "bad"


def test_local_attribution_positive_rate_metric():
    scores = [0.1] * 90 + [0.9] * 10
    recs = _make_records(scores)
    res = attribute_local(recs, metric="positive_rate", threshold=0.5)
    assert res.metric_name == "positive_rate"
    assert res.pooled == pytest.approx(0.10)


def test_local_attribution_unknown_metric_raises():
    with pytest.raises(ValueError):
        attribute_local(_make_records([0.1]), metric="bogus")


def test_aggregate_top_dimensions_groups_by_site():
    scores = [0.5] * 100 + [0.99] * 10
    sites = ["main"] * 100 + ["bad"] * 10
    recs = _make_records(scores, sites=sites)
    res = attribute_local(recs)
    rolled = aggregate_top_dimensions(
        res.top_positive + res.top_negative, dimensions=("site_id",),
    )
    assert "site_id" in rolled
    # The "bad" site should appear in the top contribution roll-up.
    assert any(item["value"] == "bad" for item in rolled["site_id"])


# ---------------------------------------------------------------------------
# Drift graph
# ---------------------------------------------------------------------------

def test_drift_graph_picks_up_correlated_dimensions():
    from datetime import datetime, timezone
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 60 records: half are (siteA, scannerX, cohortICU), half are
    # (siteB, scannerY, cohortED). So conditioning on scanner *strongly*
    # changes the cohort distribution -> high JS edge weight.
    recs = []
    for i in range(60):
        if i < 30:
            r = PredictionRecord(
                prediction_id=f"r{i}", model_id="m", model_version="v", timestamp=ts,
                site_id="A", scanner_id="X", cohort="ICU",
                prediction=1, score=0.5,
            )
        else:
            r = PredictionRecord(
                prediction_id=f"r{i}", model_id="m", model_version="v", timestamp=ts,
                site_id="B", scanner_id="Y", cohort="ED",
                prediction=1, score=0.5,
            )
        recs.append(r)
    g = build_drift_graph(recs, min_n=20)
    assert g.severity == "alert"
    assert g.edges
    # At least one edge should be a scanner -> cohort influence near JS=1.
    scanner_to_cohort = [
        e for e in g.edges
        if e.source.startswith("scanner_id=") and e.target_dimension == "cohort"
    ]
    assert scanner_to_cohort
    assert max(e.weight for e in scanner_to_cohort) > 0.25
    # DOT export should contain expected nodes/edges.
    dot = g.to_dot()
    assert "digraph drift" in dot
    assert "scanner_id=X" in dot or "scanner_id=Y" in dot


def test_drift_graph_empty_input():
    g = build_drift_graph([])
    assert g.severity == "ok"
    assert g.notes == "empty input"


def test_drift_graph_independent_dimensions_low_weight():
    from datetime import datetime, timezone
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 80 records spread evenly across 2x2 dims (independent).
    recs = []
    for i in range(80):
        recs.append(PredictionRecord(
            prediction_id=f"r{i}", model_id="m", model_version="v", timestamp=ts,
            site_id="A" if i % 2 else "B",
            scanner_id="X" if (i // 2) % 2 else "Y",
            prediction=1, score=0.5,
        ))
    g = build_drift_graph(recs, min_n=10)
    assert g.severity == "ok"
    if g.edges:
        assert max(e.weight for e in g.edges) < 0.2
