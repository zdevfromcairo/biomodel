"""Integration tests for v0.9 server endpoints: registry, policy, wasserstein, sbom."""

from __future__ import annotations

import numpy as np
import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from biomodel_monitor.server import AppSettings, create_app


class _FakeStore:
    def __init__(self):
        self.annotations = []

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


def _app(tmp_path, *, with_policy: bool = False):
    settings = AppSettings(
        require_auth=True,
        api_keys=["k1"],
        registry_path=str(tmp_path / "reg.db"),
    )
    if with_policy:
        policy_yaml = yaml.safe_dump({"policies": [
            {"name": "notify-on-warn",
             "when": {"metric": "ece", "severity_at_least": "warn"},
             "action": "notify",
             "message": "ECE shifted"},
        ]})
        p = tmp_path / "policies.yaml"
        p.write_text(policy_yaml)
        settings.policy_path = str(p)
    return create_app(
        settings,
        store_factory=lambda: _FakeStore(),
        batch_runner=lambda b, **kw: None,
    )


def _h() -> dict:
    return {"X-API-Key": "k1"}


# ------------------------------------------------------------- registry ---

def test_register_get_list_models(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/models", json={
        "model_id": "m1", "model_version": "1",
        "training_data_hash": "abc", "framework": "pytorch",
    }, headers=_h())
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "active"

    r = c.get("/models/m1/1", headers=_h())
    assert r.status_code == 200
    assert r.json()["framework"] == "pytorch"

    r = c.get("/models", headers=_h())
    assert r.status_code == 200
    assert any(m["model_id"] == "m1" for m in r.json())


def test_quarantine_and_unquarantine(tmp_path):
    c = TestClient(_app(tmp_path))
    c.post("/models", json={"model_id": "m1", "model_version": "1"},
           headers=_h())

    r = c.post("/models/m1/1/quarantine", json={"note": "ECE breach"},
               headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "quarantined"
    assert r.json()["notes"] == "ECE breach"

    r = c.post("/models/m1/1/unquarantine", headers=_h())
    assert r.json()["status"] == "active"


def test_quarantine_unknown_model_404(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/models/ghost/0/quarantine", json={}, headers=_h())
    assert r.status_code == 404


def test_lineage_round_trip(tmp_path):
    c = TestClient(_app(tmp_path))
    c.post("/models", json={"model_id": "m1", "model_version": "1"},
           headers=_h())
    c.post("/models", json={"model_id": "m2", "model_version": "1"},
           headers=_h())
    r = c.post("/lineage", json={
        "upstream_model_id": "m1", "upstream_model_version": "1",
        "downstream_model_id": "m2", "downstream_model_version": "1",
    }, headers=_h())
    assert r.status_code == 201

    r = c.get("/lineage/m2/1", headers=_h())
    body = r.json()
    assert len(body["upstreams"]) == 1
    assert body["upstreams"][0]["upstream_model_id"] == "m1"


def test_models_endpoint_503_without_registry(tmp_path):
    settings = AppSettings(require_auth=True, api_keys=["k1"])
    app = create_app(settings, store_factory=lambda: _FakeStore(),
                     batch_runner=lambda b, **kw: None)
    c = TestClient(app)
    r = c.get("/models", headers=_h())
    assert r.status_code == 503


# --------------------------------------------------------------- policy ---

def test_policy_evaluate_endpoint(tmp_path):
    c = TestClient(_app(tmp_path, with_policy=True))
    r = c.post("/policy/evaluate", json={
        "alerts": [
            {"key": "a1", "severity": "warn",
             "details": {"name": "ece"}, "category": "calibration"},
        ],
        "model_id": "m1", "model_version": "1",
    }, headers=_h())
    assert r.status_code == 200, r.text
    actions = r.json()
    assert len(actions) == 1
    assert actions[0]["action"] == "notify"
    assert actions[0]["model_id"] == "m1"


def test_policy_evaluate_503_without_engine(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/policy/evaluate", json={"alerts": []}, headers=_h())
    assert r.status_code == 503


# ---------------------------------------------------- wasserstein + sbom ---

def test_wasserstein_endpoint(tmp_path):
    c = TestClient(_app(tmp_path))
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, (200, 3)).tolist()
    cur = rng.normal(0.5, 1, (200, 3)).tolist()
    r = c.post("/wasserstein", json={
        "reference": ref, "current": cur, "n_projections": 16,
    }, headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] > 0
    assert body["n_projections"] == 16
    assert body["severity"] in ("ok", "warn", "alert")


def test_wasserstein_endpoint_dim_mismatch_400(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/wasserstein", json={
        "reference": [[0.0, 1.0]], "current": [[0.0, 1.0, 2.0]],
    }, headers=_h())
    assert r.status_code == 400


def test_sbom_endpoint(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.get("/sbom", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["bomFormat"] == "CycloneDX"
    assert body["specVersion"] == "1.5"
