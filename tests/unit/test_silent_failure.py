"""Silent failure signature tests."""


import pytest

from biomodel_monitor.metrics.silent_failure import (
    confidence_accuracy_decoupling,
    entropy_collapse,
    prediction_drift_without_input_drift,
)


def test_entropy_collapse_absolute_low():
    res = entropy_collapse([0.99] * 100)
    assert res.severity == "alert"


def test_entropy_collapse_normal_distribution_ok():
    res = entropy_collapse([0.5] * 100)
    assert res.severity == "ok"
    assert res.value == pytest.approx(1.0, abs=1e-6)


def test_entropy_collapse_relative_drop():
    res = entropy_collapse([0.99] * 100, reference_mean_entropy=0.9)
    assert res.severity == "alert"


def test_confidence_accuracy_decoupling_well_calibrated():
    # Strong predictions are correct -> positive gap, severity ok
    s = [0.05] * 50 + [0.95] * 50
    y = [0] * 50 + [1] * 50
    res = confidence_accuracy_decoupling(s, y)
    assert res.severity == "ok"


def test_confidence_accuracy_decoupling_inverted():
    # Mid-confidence predictions are mostly correct (overall acc ~0.8),
    # but the high-confidence ones are wrong -> negative gap, alert.
    s = [0.6] * 80 + [0.4] * 20 + [0.95] * 20 + [0.05] * 20
    y = [1] * 80 + [0] * 20 + [0] * 20 + [1] * 20
    res = confidence_accuracy_decoupling(s, y)
    assert res.severity == "alert"


def test_prediction_drift_without_input_drift_flag():
    res = prediction_drift_without_input_drift("alert", ["ok", "ok"])
    assert res.severity == "alert"
    res2 = prediction_drift_without_input_drift("warn", ["warn"])
    assert res2.severity == "ok"
