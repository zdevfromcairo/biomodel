"""Calibration math correctness tests."""

import numpy as np
import pytest

from biomodel_monitor.metrics.calibration import (
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_curve,
)


def test_ece_perfect_calibration_zero():
    # If score == label always, ECE is 0.
    s = [0.0] * 50 + [1.0] * 50
    y = [0] * 50 + [1] * 50
    res = expected_calibration_error(s, y, n_bins=10)
    assert res.value == pytest.approx(0.0, abs=1e-9)
    assert res.severity == "ok"


def test_ece_handcomputed_two_bins():
    # 100 samples split into two bins:
    #   bin 1: 50 samples, all conf=0.2, accuracy=0.0  -> gap 0.2
    #   bin 2: 50 samples, all conf=0.8, accuracy=1.0  -> gap 0.2
    # Weighted ECE = 0.5*0.2 + 0.5*0.2 = 0.2
    s = [0.2] * 50 + [0.8] * 50
    y = [0] * 50 + [1] * 50
    res = expected_calibration_error(s, y, n_bins=10)
    assert res.value == pytest.approx(0.2, abs=1e-9)
    assert res.severity == "alert"


def test_mce_reports_max_gap():
    s = [0.2] * 50 + [0.8] * 50
    y = [1] * 50 + [1] * 50  # bin1 gap=0.8, bin2 gap=0.2
    res = maximum_calibration_error(s, y, n_bins=10)
    assert res.value == pytest.approx(0.8, abs=1e-9)
    assert res.severity == "alert"


def test_brier_score_known_value():
    # For score=0.5, label=1 always: (0.5-1)^2 = 0.25
    s = [0.5] * 100
    y = [1] * 100
    res = brier_score(s, y)
    assert res.value == pytest.approx(0.25, abs=1e-9)


def test_reliability_curve_shape():
    rng = np.random.default_rng(0)
    s = rng.uniform(0, 1, size=200)
    y = (rng.uniform(0, 1, size=200) < s).astype(int)
    curve = reliability_curve(s, y, n_bins=5)
    assert len(curve["bin_confidence"]) == 5
    assert sum(curve["bin_count"]) == 200


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        expected_calibration_error([], [])
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [0, 1])
    with pytest.raises(ValueError):
        expected_calibration_error([1.5], [1])
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [2])
