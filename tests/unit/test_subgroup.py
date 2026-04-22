"""Subgroup slicing tests, including small-N guard and Wilson CI."""


import pytest

from biomodel_monitor.metrics.subgroup import slice_metrics, wilson_interval


def test_wilson_interval_basic():
    lo, hi = wilson_interval(50, 100)
    assert 0.0 <= lo < 0.5 < hi <= 1.0
    # n=0 returns full interval
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_slice_metrics_flags_degraded_subgroup():
    # site A: 100 perfect predictions; site B: 100 all wrong
    groups = ["A"] * 100 + ["B"] * 100
    scores = [0.9] * 100 + [0.9] * 100
    labels = [1] * 100 + [0] * 100
    res = slice_metrics(
        dimension="site_id",
        groups=groups,
        scores=scores,
        labels=labels,
        metric="accuracy",
        min_n=30,
    )
    by_v = {r.value: r for r in res}
    assert by_v["A"].metric_value == pytest.approx(1.0)
    assert by_v["B"].metric_value == pytest.approx(0.0)
    assert by_v["B"].severity == "alert"
    # Worst-first ordering
    assert res[0].value == "B"


def test_small_n_slice_is_not_alerted():
    groups = ["A"] * 100 + ["B"] * 5
    scores = [0.9] * 100 + [0.9] * 5
    labels = [1] * 100 + [0] * 5
    res = slice_metrics(
        dimension="site_id",
        groups=groups,
        scores=scores,
        labels=labels,
        metric="accuracy",
        min_n=30,
    )
    by_v = {r.value: r for r in res}
    assert by_v["B"].severity == "ok"
    assert "below min_n" in by_v["B"].extra.get("note", "")


def test_top_k_truncates():
    groups = ["A"] * 60 + ["B"] * 60 + ["C"] * 60
    scores = [0.9] * 180
    labels = [1] * 60 + [0] * 60 + [1] * 60
    res = slice_metrics(
        dimension="site_id", groups=groups, scores=scores, labels=labels,
        metric="accuracy", min_n=30, top_k=2,
    )
    assert len(res) == 2


def test_mean_score_metric_no_labels():
    groups = ["A"] * 100 + ["B"] * 100
    scores = [0.2] * 100 + [0.8] * 100
    res = slice_metrics(
        dimension="site_id", groups=groups, scores=scores,
        metric="mean_score", min_n=30,
    )
    by_v = {r.value: r for r in res}
    assert by_v["A"].metric_value == pytest.approx(0.2)
    assert by_v["B"].metric_value == pytest.approx(0.8)


def test_mismatched_lengths_error():
    with pytest.raises(ValueError):
        slice_metrics(dimension="d", groups=["A"], scores=[0.1, 0.2], metric="mean_score")


def test_unknown_metric_raises():
    with pytest.raises(ValueError):
        slice_metrics(dimension="d", groups=["A"], scores=[0.5], metric="auc")


def test_handles_none_groups_as_unknown():
    res = slice_metrics(
        dimension="site_id",
        groups=[None] * 50 + ["A"] * 50,
        scores=[0.5] * 100,
        metric="mean_score",
        min_n=10,
    )
    values = {r.value for r in res}
    assert "(unknown)" in values
