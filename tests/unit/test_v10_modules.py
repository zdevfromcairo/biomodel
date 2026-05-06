"""Unit tests for v0.10 modules: active_learning, conformal, feedback, shadow."""

from __future__ import annotations

import math

import numpy as np
import pytest

# --------------------------------------------------------- active_learning ---

def test_entropy_uniform_max_for_3_classes():
    from biomodel_monitor.active_learning import entropy_score
    assert entropy_score([1 / 3, 1 / 3, 1 / 3]) == pytest.approx(math.log(3))


def test_entropy_one_hot_zero():
    from biomodel_monitor.active_learning import entropy_score
    assert entropy_score([1.0, 0.0, 0.0]) == 0.0


def test_margin_and_least_confidence_orderings():
    from biomodel_monitor.active_learning import (
        least_confidence_score,
        margin_score,
    )
    sure = [0.9, 0.05, 0.05]
    unsure = [0.4, 0.35, 0.25]
    assert margin_score(unsure) > margin_score(sure)
    assert least_confidence_score(unsure) > least_confidence_score(sure)


def test_bald_zero_when_all_samples_agree():
    from biomodel_monitor.active_learning import bald_score
    same = [[0.7, 0.2, 0.1]] * 5
    assert bald_score(same) == pytest.approx(0.0, abs=1e-12)


def test_bald_positive_when_samples_disagree():
    from biomodel_monitor.active_learning import bald_score
    disagreeing = [
        [0.9, 0.05, 0.05],
        [0.05, 0.9, 0.05],
        [0.05, 0.05, 0.9],
    ]
    assert bald_score(disagreeing) > 0.5


def test_score_record_rejects_bald():
    from biomodel_monitor.active_learning import score_record
    with pytest.raises(ValueError):
        score_record([0.5, 0.5], strategy="bald")


def test_active_learning_queue_priority_and_label_flow(tmp_path):
    from biomodel_monitor.active_learning import (
        ActiveLearningQueue,
        QueueItem,
    )
    q = ActiveLearningQueue(tmp_path / "q.db")
    items = [
        QueueItem(model_id="m", model_version="1", record_id="r1",
                  score=0.1, strategy="entropy"),
        QueueItem(model_id="m", model_version="1", record_id="r2",
                  score=0.9, strategy="entropy"),
        QueueItem(model_id="m", model_version="1", record_id="r3",
                  score=0.5, strategy="entropy"),
    ]
    assert q.enqueue_many(items) == 3

    batch = q.next_batch("m", "1", limit=2)
    assert [i.record_id for i in batch] == ["r2", "r3"]

    out = q.submit_label("m", "1", "r2", label=1, note="positive")
    assert out.status == "labelled"
    assert out.label == 1
    assert out.updated_at is not None

    # next_batch no longer returns r2
    batch2 = q.next_batch("m", "1", limit=10)
    assert [i.record_id for i in batch2] == ["r3", "r1"]

    stats = q.stats("m", "1")
    assert stats["by_status"]["labelled"]["n"] == 1
    assert stats["by_status"]["pending"]["n"] == 2

    labelled = q.labels("m", "1")
    assert [i.record_id for i in labelled] == ["r2"]
    q.close()


def test_active_learning_queue_unknown_record_raises(tmp_path):
    from biomodel_monitor.active_learning import ActiveLearningQueue
    q = ActiveLearningQueue(tmp_path / "q.db")
    with pytest.raises(KeyError):
        q.submit_label("m", "1", "ghost", label=0)
    q.close()


# ---------------------------------------------------------------- conformal --

def _toy_classifier(rng: np.random.Generator, n: int, K: int = 3,
                    well_calibrated: bool = True):
    """Tiny synthetic classifier producing labels + soft-maxy probs."""
    y = rng.integers(0, K, size=n)
    raw = rng.normal(0, 1, size=(n, K))
    if well_calibrated:
        # Push the true class up so we get reasonable signal.
        raw[np.arange(n), y] += 1.5
    e = np.exp(raw - raw.max(axis=1, keepdims=True))
    p = e / e.sum(axis=1, keepdims=True)
    return p, y


def test_conformal_marginal_coverage_aps():
    from biomodel_monitor.conformal import calibrate, empirical_coverage
    rng = np.random.default_rng(0)
    p_cal, y_cal = _toy_classifier(rng, 1000)
    p_te, y_te = _toy_classifier(rng, 1000)
    cal = calibrate(p_cal, y_cal, alpha=0.1, score_fn="aps")
    cov = empirical_coverage(p_te, y_te, cal)
    assert cov >= 0.85  # 1 - alpha = 0.9 with finite-sample slack


def test_conformal_marginal_coverage_lac():
    from biomodel_monitor.conformal import calibrate, empirical_coverage
    rng = np.random.default_rng(1)
    p_cal, y_cal = _toy_classifier(rng, 1000)
    p_te, y_te = _toy_classifier(rng, 1000)
    cal = calibrate(p_cal, y_cal, alpha=0.1, score_fn="lac")
    cov = empirical_coverage(p_te, y_te, cal)
    assert cov >= 0.85


def test_conformal_predict_sets_severity_and_extras():
    from biomodel_monitor.conformal import calibrate, predict_sets
    rng = np.random.default_rng(2)
    p_cal, y_cal = _toy_classifier(rng, 500)
    p_te, _ = _toy_classifier(rng, 100)
    cal = calibrate(p_cal, y_cal, alpha=0.2, score_fn="aps")
    res = predict_sets(p_te, cal)
    assert res.value > 0
    assert all(len(s) >= 1 for s in res.sets)  # never empty
    assert res.severity in ("ok", "warn", "alert")
    assert "frac_singleton" in res.extra
    d = res.as_dict()
    assert "sets" in d and "extra" in d


def test_conformal_rejects_bad_inputs():
    from biomodel_monitor.conformal import calibrate
    with pytest.raises(ValueError):
        calibrate([[0.5, 0.5]], [0], alpha=1.5)
    with pytest.raises(ValueError):
        calibrate([[0.5, 0.5]], [9], alpha=0.1)  # label out of range


# ---------------------------------------------------------------- feedback --

def test_feedback_merge_labels_skips_unlabelled():
    from biomodel_monitor.active_learning import QueueItem
    from biomodel_monitor.feedback import merge_labels
    items = [
        QueueItem(model_id="m", model_version="1", record_id="r1",
                  score=0.1, strategy="entropy", status="labelled", label=1),
        QueueItem(model_id="m", model_version="1", record_id="r3",
                  score=0.1, strategy="entropy", status="labelled", label=0),
    ]
    s, y, batch = merge_labels(
        record_ids=["r1", "r2", "r3"],
        scores=[0.9, 0.5, 0.1],
        prior_labels=[0, None, 1],
        expert_items=items,
    )
    assert s == [0.9, 0.1]
    assert y == [1, 0]
    assert batch.n_total == 3
    assert batch.n_used == 2
    assert batch.n_relabelled == 2  # both flipped


def test_feedback_recompute_ece_returns_severity():
    from biomodel_monitor.active_learning import QueueItem
    from biomodel_monitor.feedback import recompute_ece

    rids = [f"r{i}" for i in range(50)]
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 1, 50).tolist()
    labels = (np.array(scores) > 0.5).astype(int).tolist()
    items = [
        QueueItem(model_id="m", model_version="1", record_id=rid,
                  score=0.0, strategy="entropy",
                  status="labelled", label=int(lbl))
        for rid, lbl in zip(rids, labels)
    ]
    res, batch = recompute_ece(rids, scores, [None] * 50, items)
    assert res.severity in ("ok", "warn", "alert")
    assert batch.n_used == 50
    assert "feedback" in res.extra


def test_feedback_raises_if_no_overlap():
    from biomodel_monitor.feedback import merge_labels
    with pytest.raises(ValueError):
        merge_labels(["r1"], [0.5], [None], expert_items=[])


# --------------------------------------------------------------- shadow ----

def test_mcnemar_canary_significantly_worse():
    from biomodel_monitor.shadow import mcnemar
    # 100 records: control gets 80 right; canary, the same 60 + worsens 20.
    n = 100
    control = [1] * 80 + [0] * 20
    canary = [1] * 60 + [0] * 40  # canary is 20 right where control was right
    # Reorder so the disagreement is clear: first 60 both right, next 20 control-only,
    # last 20 both wrong.
    res = mcnemar(control, canary)
    assert res.value > 0  # control wins
    assert res.severity in ("warn", "alert")
    assert res.extra["b_control_wins"] == 20
    assert res.extra["c_canary_wins"] == 0
    assert res.extra["n"] == n


def test_mcnemar_no_diff_is_ok():
    from biomodel_monitor.shadow import mcnemar
    res = mcnemar([1, 0, 1, 0, 1], [1, 0, 1, 0, 1])
    assert res.severity == "ok"
    assert res.value == 0


def test_paired_bootstrap_detects_loss_increase():
    from biomodel_monitor.shadow import paired_bootstrap_diff
    rng = np.random.default_rng(0)
    base = rng.uniform(0.1, 0.4, 200)         # control losses
    canary = base + rng.normal(0.05, 0.01, 200)  # canary slightly worse
    res = paired_bootstrap_diff(base, canary, n_boot=500, seed=7)
    assert res.value < 0  # control - canary, control loss is lower → negative
    # severity bands are on positive (control wins). Here control "wins" on
    # loss only when its loss is *lower*; we set positive = control wins
    # ⇒ ok in this orientation. Caller chooses orientation.
    assert res.severity == "ok"
    assert "ci_low" in res.extra and "ci_high" in res.extra


def test_paired_bootstrap_alerts_when_diff_large_positive():
    from biomodel_monitor.shadow import paired_bootstrap_diff
    rng = np.random.default_rng(1)
    canary = rng.normal(0, 0.01, 200)
    control = canary + 0.10  # control beats canary by 0.10 on average
    res = paired_bootstrap_diff(control, canary, n_boot=500,
                                warn=0.02, alert=0.05, seed=0)
    assert res.severity == "alert"
    assert res.value > 0
