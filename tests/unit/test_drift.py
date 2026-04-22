"""Drift metric correctness tests."""

import numpy as np
import pytest

from biomodel_monitor.metrics.drift import (
    embedding_cosine_shift,
    js_divergence_categorical,
    ks_test,
    mmd_rbf,
    psi,
)


def test_psi_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    x = rng.normal(size=2000)
    res = psi(x, x.copy())
    assert res.value == pytest.approx(0.0, abs=1e-9)
    assert res.severity == "ok"


def test_psi_detects_large_shift():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, size=2000)
    cur = rng.normal(2, 1, size=2000)
    res = psi(ref, cur)
    assert res.value > 0.25
    assert res.severity == "alert"


def test_psi_requires_nonempty():
    with pytest.raises(ValueError):
        psi([], [1.0])


def test_ks_identical_distributions_high_pvalue():
    rng = np.random.default_rng(0)
    x = rng.normal(size=500)
    res = ks_test(x, x.copy())
    assert res.value == pytest.approx(0.0, abs=1e-9)
    assert res.p_value == pytest.approx(1.0)
    assert res.severity == "ok"


def test_ks_detects_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, size=500)
    b = rng.normal(1, 1, size=500)
    res = ks_test(a, b)
    assert res.p_value < 0.001
    assert res.severity == "alert"


def test_js_zero_for_identical_categorical():
    a = ["x", "y", "z"] * 100
    res = js_divergence_categorical(a, a)
    assert res.value == pytest.approx(0.0, abs=1e-9)
    assert res.severity == "ok"


def test_js_one_for_disjoint_supports():
    a = ["x"] * 100
    b = ["y"] * 100
    res = js_divergence_categorical(a, b)
    # JS divergence in base-2 between two point masses at different categories is 1.
    assert res.value == pytest.approx(1.0, abs=1e-3)
    assert res.severity == "alert"


def test_mmd_rbf_no_shift_high_pvalue():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(50, 4))
    y = rng.normal(size=(50, 4))
    res = mmd_rbf(x, y, n_permutations=50, rng=np.random.default_rng(1))
    assert res.p_value > 0.05
    assert res.severity == "ok"


def test_mmd_rbf_detects_shift():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, size=(80, 4))
    y = rng.normal(2, 1, size=(80, 4))
    res = mmd_rbf(x, y, n_permutations=50, rng=np.random.default_rng(1))
    assert res.p_value < 0.05
    assert res.severity in ("warn", "alert")


def test_embedding_cosine_shift_aligned_means_zero():
    x = np.array([[1.0, 0.0]] * 10)
    y = np.array([[2.0, 0.0]] * 10)
    res = embedding_cosine_shift(x, y)
    assert res.value == pytest.approx(0.0, abs=1e-9)


def test_embedding_cosine_shift_orthogonal_one():
    x = np.array([[1.0, 0.0]] * 10)
    y = np.array([[0.0, 1.0]] * 10)
    res = embedding_cosine_shift(x, y)
    assert res.value == pytest.approx(1.0, abs=1e-9)
    assert res.severity == "alert"
