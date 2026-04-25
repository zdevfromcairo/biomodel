"""Tests for the rolling baseline learner and promotion workflow."""

from __future__ import annotations

import pytest

from biomodel_monitor.baselines.learner import RollingBaselineLearner, promote_candidate
from biomodel_monitor.store.repository import MetricsStore


def test_learner_extends_window(tmp_path, make_batch):
    store = MetricsStore(tmp_path / "s.db")
    learner = RollingBaselineLearner(
        store=store, model_id="m1", model_version="1.0.0", max_window=10000,
    )
    p1 = learner.update(make_batch(n=120, seed=1))
    p2 = learner.update(make_batch(n=80, seed=2))
    assert p2["n"] == p1["n"] + 80


def test_learner_respects_max_window(tmp_path, make_batch):
    store = MetricsStore(tmp_path / "s.db")
    learner = RollingBaselineLearner(
        store=store, model_id="m1", model_version="1.0.0", max_window=100,
    )
    learner.update(make_batch(n=200, seed=1))
    payload = learner.update(make_batch(n=50, seed=2))
    assert payload["n"] == 100


def test_promotion_requires_min_n(tmp_path, make_batch):
    store = MetricsStore(tmp_path / "s.db")
    learner = RollingBaselineLearner(
        store=store, model_id="m1", model_version="1.0.0",
    )
    learner.update(make_batch(n=50, seed=1))
    with pytest.raises(ValueError):
        promote_candidate(
            store=store, model_id="m1", model_version="1.0.0", min_n=200,
        )
    learner.update(make_batch(n=300, seed=2))
    bid = promote_candidate(
        store=store, model_id="m1", model_version="1.0.0", min_n=200,
    )
    assert bid > 0
    promoted = store.get_promoted_baseline(model_id="m1", model_version="1.0.0")
    assert promoted is not None and promoted["payload"]["n"] >= 200
