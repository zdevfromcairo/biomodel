"""Tests for the v0.6 forecasting / conformal / concept-drift / causal modules."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from biomodel_monitor.intelligence.causal import attribute_interactions
from biomodel_monitor.metrics.concept_drift import (
    detect_concept_drift,
    fit_pca_baseline,
)
from biomodel_monitor.metrics.conformal import (
    empirical_coverage,
    fit_split_conformal_regression,
)
from biomodel_monitor.metrics.forecast import forecast_metric
from biomodel_monitor.schema.models import PredictionRecord

# --------------------------------------------------------------------- forecast


def test_forecast_flat_series_predicts_flat():
    out = forecast_metric([0.1] * 20, horizon=5, threshold=0.5)
    assert out.severity == "ok"
    assert out.eta_to_breach is None
    assert all(abs(p.value - 0.1) < 0.05 for p in out.forecast)


def test_forecast_increasing_series_eventually_breaches():
    history = [0.05 + 0.02 * i for i in range(20)]  # rising trend
    out = forecast_metric(history, horizon=30, threshold=0.6, direction="above")
    assert out.severity in {"warn", "alert"}
    if out.severity == "warn":
        assert out.eta_to_breach is not None and 1 <= out.eta_to_breach <= 30


def test_forecast_already_breached_marks_alert():
    out = forecast_metric([0.7, 0.72, 0.71], horizon=3, threshold=0.5,
                          direction="above")
    assert out.severity == "alert"


def test_forecast_handles_short_history():
    out = forecast_metric([0.4], horizon=3, threshold=0.5)
    assert out.method == "naive"
    assert len(out.forecast) == 3
    assert out.eta_to_breach is None


def test_forecast_validates_args():
    with pytest.raises(ValueError):
        forecast_metric([0.1, 0.2], horizon=0)
    with pytest.raises(ValueError):
        forecast_metric([0.1, 0.2], alpha=1.5)


# --------------------------------------------------------------------- conformal


def test_split_conformal_calibrates_radius():
    rng = np.random.default_rng(0)
    y_true = rng.normal(size=200)
    y_pred = y_true + rng.normal(scale=0.5, size=200)
    cal = fit_split_conformal_regression(
        list(y_true), list(y_pred), alpha=0.1,
    )
    assert cal.q_hat > 0
    # Symmetric interval helper sanity.
    lo, hi = cal.interval(0.0)
    assert lo == -cal.q_hat and hi == cal.q_hat


def test_empirical_coverage_flags_under_coverage():
    intervals = [(0.0, 1.0)] * 50  # narrow interval
    y = [0.5] * 40 + [10.0] * 10  # 80% coverage
    out = empirical_coverage(y, intervals, nominal_coverage=0.95,
                             warn_gap=0.05, alert_gap=0.10)
    assert out.empirical_coverage == pytest.approx(0.8)
    assert out.severity == "alert"
    assert out.miscoverage_gap > 0


def test_empirical_coverage_within_tolerance_is_ok():
    intervals = [(-1.0, 1.0)] * 100
    y = [0.0] * 100
    out = empirical_coverage(y, intervals, nominal_coverage=0.9)
    assert out.empirical_coverage == 1.0
    assert out.severity == "ok"


def test_empirical_coverage_low_n_warns():
    out = empirical_coverage([0.5], [(0.0, 1.0)], nominal_coverage=0.9, min_n=30)
    assert out.severity == "warn"


# ---------------------------------------------------------------- concept drift


def test_pca_baseline_then_no_drift_is_ok():
    rng = np.random.default_rng(0)
    base = rng.normal(size=(200, 6))
    basis = fit_pca_baseline(base, n_components=3)
    new = rng.normal(size=(50, 6))
    res = detect_concept_drift(new, basis, warn_rate=0.1, alert_rate=0.3)
    assert res.severity in {"ok", "warn"}
    assert 0 <= res.drift_rate <= 1


def test_pca_baseline_then_shifted_data_alerts():
    rng = np.random.default_rng(1)
    base = rng.normal(size=(300, 6))
    basis = fit_pca_baseline(base, n_components=2, cutoff_quantile=0.95)
    # Add an off-subspace shift to all 50 new rows.
    new = rng.normal(size=(50, 6)) + np.array([3, 3, 3, 3, 3, 3])
    res = detect_concept_drift(new, basis)
    assert res.severity == "alert"
    assert res.drift_rate > 0.2


def test_pca_validates_dimensions():
    basis = fit_pca_baseline(np.random.default_rng(0).normal(size=(50, 4)), n_components=2)
    with pytest.raises(ValueError):
        detect_concept_drift(np.zeros((10, 5)), basis)


# ----------------------------------------------------------- causal interactions


def _r(score, **kw) -> PredictionRecord:
    base = dict(
        prediction_id=f"p-{score}-{kw}",
        model_id="m", model_version="1.0.0",
        timestamp=datetime.now(tz=timezone.utc),
        site_id=kw.get("site_id", "S"),
        prediction=1, score=score,
    )
    for k in ("scanner_id", "stain", "tissue_type", "cohort", "site_id"):
        if k in kw:
            base[k] = kw[k]
    return PredictionRecord(**base)


def test_interaction_ranks_joint_subgroup_above_marginals():
    rng = np.random.default_rng(0)
    records: list[PredictionRecord] = []
    # Baseline: uniform mid score.
    for _ in range(80):
        records.append(_r(0.5 + rng.normal(scale=0.02),
                          scanner_id="X", stain="A"))
    for _ in range(80):
        records.append(_r(0.5 + rng.normal(scale=0.02),
                          scanner_id="Y", stain="B"))
    # Pathological joint: ScannerY × Stain=A is shifted high.
    for _ in range(40):
        records.append(_r(0.85 + rng.normal(scale=0.02),
                          scanner_id="Y", stain="A"))
    out = attribute_interactions(records, top_k=3, min_n=20,
                                 warn_lift=0.05, alert_lift=0.10)
    assert out.top, "expected at least one ranked interaction"
    top = out.top[0]
    assert {top.dim1, top.dim2} == {"scanner_id", "stain"}
    assert {top.value1, top.value2} == {"Y", "A"}
    assert top.interaction_lift > 0
    assert out.severity in {"warn", "alert"}


def test_interaction_returns_ok_on_empty_input():
    out = attribute_interactions([])
    assert out.severity == "ok"
    assert out.top == []
