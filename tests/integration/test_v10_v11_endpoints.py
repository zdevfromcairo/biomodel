"""Integration tests for v0.10 + v0.11 server endpoints."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from biomodel_monitor.server import AppSettings, create_app


class _FakeStore:
    def list_runs(self, **kw):
        return []

    def list_baselines(self, **kw):
        return []

    def add_annotation(self, ann):
        ann.id = 1
        ann.created_at = "2026-01-01T00:00:00Z"
        return ann

    def metric_history(self, **kw):
        return []

    def close(self):
        pass


def _app(tmp_path):
    settings = AppSettings(
        require_auth=True, api_keys=["k1"],
        active_learning_path=str(tmp_path / "al.db"),
        vector_store_path=str(tmp_path / "vec.db"),
    )
    return create_app(
        settings, store_factory=lambda: _FakeStore(),
        batch_runner=lambda b, **kw: None,
    )


def _h():
    return {"X-API-Key": "k1"}


# -------------------------------------------------------------- v0.10 ----

def test_active_learning_round_trip(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/active-learning/enqueue", json={
        "model_id": "m", "model_version": "1",
        "items": [
            {"record_id": "r1", "probs": [0.5, 0.5], "strategy": "entropy"},
            {"record_id": "r2", "score": 0.1, "strategy": "entropy"},
            {"record_id": "r3", "probs": [0.9, 0.1], "strategy": "entropy"},
        ],
    }, headers=_h())
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3

    r = c.get("/active-learning/m/1/queue?limit=2", headers=_h())
    assert r.status_code == 200
    items = r.json()
    assert items[0]["record_id"] == "r1"  # entropy of uniform highest

    r = c.post("/active-learning/m/1/r1/label",
               json={"label": 1, "note": "expert says positive"},
               headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "labelled"
    assert r.json()["label"] == 1

    r = c.get("/active-learning/m/1/stats", headers=_h())
    assert r.status_code == 200
    by_status = r.json()["by_status"]
    assert by_status["labelled"]["n"] == 1
    assert by_status["pending"]["n"] == 2


def test_active_learning_unconfigured_returns_503(tmp_path):
    settings = AppSettings(require_auth=True, api_keys=["k1"])
    app = create_app(settings, store_factory=lambda: _FakeStore(),
                     batch_runner=lambda b, **kw: None)
    c = TestClient(app)
    r = c.get("/active-learning/m/1/queue", headers=_h())
    assert r.status_code == 503


def test_conformal_calibrate_and_predict(tmp_path):
    c = TestClient(_app(tmp_path))
    rng = np.random.default_rng(0)
    # 50 calibration samples, 3 classes, well-separated
    y = rng.integers(0, 3, 50)
    raw = rng.normal(0, 1, (50, 3))
    raw[np.arange(50), y] += 2.0
    e = np.exp(raw - raw.max(axis=1, keepdims=True))
    p = (e / e.sum(axis=1, keepdims=True)).tolist()

    r = c.post("/conformal/calibrate", json={
        "probs": p, "labels": y.tolist(), "alpha": 0.1, "score_fn": "aps",
    }, headers=_h())
    assert r.status_code == 200, r.text
    cal = r.json()
    assert cal["score_fn"] == "aps"
    assert cal["n_calibration"] == 50

    r = c.post("/conformal/predict", json={
        "probs": p[:5], "calibration": cal,
    }, headers=_h())
    assert r.status_code == 200
    out = r.json()
    assert len(out["sets"]) == 5
    assert all(len(s) >= 1 for s in out["sets"])


def test_shadow_endpoints(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/shadow/mcnemar", json={
        "control_correct": [1] * 80 + [0] * 20,
        "canary_correct": [1] * 60 + [0] * 40,
    }, headers=_h())
    assert r.status_code == 200, r.text
    assert "p_value" in r.json()["extra"]

    r = c.post("/shadow/bootstrap", json={
        "control": [0.3] * 50, "canary": [0.2] * 50,
        "n_boot": 200, "warn": 0.02, "alert": 0.05,
    }, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "alert"
    assert "ci_low" in body["extra"]


# -------------------------------------------------------------- v0.11 ----

def test_modality_check_dispatch(tmp_path):
    c = TestClient(_app(tmp_path))
    ref = {
        "width_mean": 1024, "height_mean": 768,
        "intensity_mean": 100, "intensity_std": 30, "channels": 3,
    }
    cur = dict(ref, channels=1)
    r = c.post("/modality/check", json={
        "kind": "image", "reference": ref, "current": cur,
    }, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["severity"] == "alert"
    assert r.json()["kind"] == "image"


def test_modality_unknown_kind_400(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/modality/check", json={
        "kind": "audio", "reference": {}, "current": {},
    }, headers=_h())
    assert r.status_code == 400


def test_vector_store_round_trip(tmp_path):
    c = TestClient(_app(tmp_path))
    for rid, emb in [("a", [1.0, 0.0]), ("b", [0.0, 1.0]),
                     ("c", [0.9, 0.1])]:
        r = c.post("/vector/add", json={
            "namespace": "ns", "record_id": rid,
            "embedding": emb, "metadata": {"label": rid.upper()},
        }, headers=_h())
        assert r.status_code == 200, r.text

    r = c.post("/vector/query", json={
        "namespace": "ns", "embedding": [1.0, 0.0], "k": 2,
    }, headers=_h())
    assert r.status_code == 200
    out = r.json()
    assert [n["record_id"] for n in out] == ["a", "c"]


def test_fingerprint_compute_and_compare(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/fingerprint", json={
        "canary_inputs_id": "cid", "predictions": [[0.1, 0.9]],
    }, headers=_h())
    assert r.status_code == 200, r.text
    fp = r.json()["fingerprint"]

    r = c.post("/fingerprint/compare", json={
        "expected": fp, "actual": fp,
    }, headers=_h())
    assert r.json()["matches"] is True

    r = c.post("/fingerprint/compare", json={
        "expected": fp, "actual": "sha256:" + "0" * 64,
    }, headers=_h())
    assert r.json()["matches"] is False
    assert r.json()["severity"] == "alert"
