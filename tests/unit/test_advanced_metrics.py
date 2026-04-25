"""Tests for v0.3 advanced metrics: bootstrap, fairness, multiclass calibration."""

from __future__ import annotations

import numpy as np
import pytest

from biomodel_monitor.metrics.bootstrap import bootstrap_ci
from biomodel_monitor.metrics.calibration_multiclass import (
    class_wise_ece,
    multiclass_brier,
    top_label_ece,
)
from biomodel_monitor.metrics.fairness import (
    demographic_parity,
    equal_opportunity,
    equalized_odds,
)

# --------- bootstrap -----------------------------------------------------

def test_bootstrap_ci_brackets_point_estimate():
    rng = np.random.default_rng(0)
    data = rng.normal(0.5, 0.1, size=300).tolist()
    ci = bootstrap_ci(data, lambda a: float(np.mean(a)), n_samples=400, seed=1)
    assert ci.lo <= ci.point <= ci.hi
    # CI should be reasonably tight for n=300
    assert ci.hi - ci.lo < 0.05


def test_bootstrap_ci_is_deterministic_with_seed():
    data = list(np.linspace(0, 1, 100))
    a = bootstrap_ci(data, lambda x: float(np.mean(x)), n_samples=200, seed=42)
    b = bootstrap_ci(data, lambda x: float(np.mean(x)), n_samples=200, seed=42)
    assert (a.lo, a.hi, a.point) == (b.lo, b.hi, b.point)


# --------- multiclass calibration ----------------------------------------

def test_top_label_ece_perfect_when_calibrated():
    # 4 classes, predictions concentrate on the right class
    n_per = 100
    scores = []
    labels = []
    for cls in range(4):
        for _ in range(n_per):
            row = [0.05] * 4
            row[cls] = 0.85
            scores.append(row)
            labels.append(cls)
    res = top_label_ece(scores, labels, n_bins=10)
    # confidence ≈ 0.85 and accuracy = 1.0 → ECE ≈ 0.15
    assert 0.10 <= res.value <= 0.20
    assert res.severity in ("warn", "alert")


def test_class_wise_ece_handles_missing_class_diversity():
    # only one class present → graceful handling, value=0
    scores = [[1.0, 0.0]] * 50
    labels = [0] * 50
    res = class_wise_ece(scores, labels)
    assert res.value == 0.0 and res.severity == "ok"


def test_multiclass_brier_zero_for_one_hot_predictions():
    n = 30
    scores = []
    labels = []
    for i in range(n):
        cls = i % 3
        row = [0.0, 0.0, 0.0]
        row[cls] = 1.0
        scores.append(row)
        labels.append(cls)
    res = multiclass_brier(scores, labels)
    assert res.value < 1e-6 and res.severity == "ok"


def test_multiclass_validation_rejects_bad_shape():
    with pytest.raises(ValueError):
        top_label_ece([0.1, 0.9], [0, 1])  # 1-D scores


# --------- fairness ------------------------------------------------------

def test_demographic_parity_detects_disparity():
    rng = np.random.default_rng(0)
    scores_a = rng.uniform(0.6, 0.9, size=200).tolist()
    scores_b = rng.uniform(0.1, 0.4, size=200).tolist()
    groups = (["A"] * 200) + (["B"] * 200)
    res = demographic_parity(groups, scores_a + scores_b, threshold=0.5, min_n=20)
    assert res.value > 0.5
    assert res.severity == "alert"


def test_demographic_parity_min_n_skip():
    res = demographic_parity(
        ["A"] * 5 + ["B"] * 100,
        [0.6] * 5 + [0.4] * 100,
        threshold=0.5, min_n=10,
    )
    assert res.per_group["A"]["skipped"] == 1.0


def test_equal_opportunity_detects_tpr_gap():
    # group A: high TPR; group B: low TPR (positives mostly under-scored)
    rng = np.random.default_rng(0)
    n = 200
    sa = rng.uniform(0.7, 0.95, size=n).tolist()
    sb = rng.uniform(0.2, 0.45, size=n).tolist()
    groups = ["A"] * n + ["B"] * n
    labels = [1] * n + [1] * n
    res = equal_opportunity(groups, sa + sb, labels, threshold=0.5, min_n=10)
    assert res.value > 0.5 and res.severity == "alert"


def test_equalized_odds_zero_when_distributions_match():
    rng = np.random.default_rng(0)
    n = 200
    s = rng.uniform(0, 1, size=n * 2).tolist()
    y = (rng.uniform(0, 1, size=n * 2) > 0.5).astype(int).tolist()
    groups = ["A"] * n + ["B"] * n
    res = equalized_odds(groups, s, y, threshold=0.5, min_n=10)
    assert res.value < 0.2  # noisy but small
