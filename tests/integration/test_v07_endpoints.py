"""Tests for v0.7 server endpoints: tenancy, audit, plugins, federation."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from biomodel_monitor.server import AppSettings, create_app


class _FakeStore:
    def __init__(self):
        self.annotations = []
        self.promotions = []

    # Reads (unused here).
    def list_runs(self, **kw):
        return []

    def metric_history(self, **kw):
        return []

    def list_baselines(self, **kw):
        return []

    # Writes.
    def add_annotation(self, ann):
        ann.id = len(self.annotations) + 1
        ann.created_at = "2026-04-29T00:00:00Z"
        self.annotations.append(ann)
        return ann

    def promote_baseline(self, bid):
        self.promotions.append(bid)

    def close(self):
        pass


@pytest.fixture
def fake_store():
    return _FakeStore()


def _app_with_tenants(fake_store, *, audit_path=None):
    settings = AppSettings(
        require_auth=True,
        api_keys=["unused"],
        tenant_keys={
            "key-viewer": ("hospA", "viewer"),
            "key-operator": ("hospA", "operator"),
            "key-writer": ("hospA", "writer"),
            "key-admin": ("hospA", "admin"),
        },
        audit_log_path=str(audit_path) if audit_path else None,
    )
    return create_app(settings, store_factory=lambda: fake_store,
                      batch_runner=lambda b, **k: None)


def test_whoami_returns_role_and_permissions(fake_store):
    c = TestClient(_app_with_tenants(fake_store))
    r = c.get("/tenants/whoami", headers={"X-API-Key": "key-operator"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "hospA"
    assert body["role"] == "operator"
    assert "annotate" in body["permissions"]
    assert "promote_baseline" not in body["permissions"]


def test_invalid_key_is_rejected_when_tenancy_configured(fake_store):
    c = TestClient(_app_with_tenants(fake_store))
    r = c.get("/tenants/whoami", headers={"X-API-Key": "bogus"})
    assert r.status_code == 401


def test_viewer_cannot_annotate(fake_store):
    c = TestClient(_app_with_tenants(fake_store))
    r = c.post(
        "/alerts/K/annotations",
        params={"model_id": "m", "model_version": "1.0.0"},
        headers={"X-API-Key": "key-viewer"},
        json={"kind": "ack"},
    )
    assert r.status_code == 403


def test_operator_can_annotate_and_audit_records_it(fake_store, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    c = TestClient(_app_with_tenants(fake_store, audit_path=audit_path))
    r = c.post(
        "/alerts/K/annotations",
        params={"model_id": "m", "model_version": "1.0.0"},
        headers={"X-API-Key": "key-operator"},
        json={"kind": "ack"},
    )
    assert r.status_code == 201
    assert len(fake_store.annotations) == 1
    # Audit chain has one entry.
    from biomodel_monitor.audit import AuditLog
    log = AuditLog(audit_path)
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].action == "annotate"
    assert entries[0].actor_tenant == "hospA"
    ok, bad = log.verify()
    assert ok and bad is None


def test_writer_can_promote_baseline_viewer_cannot(fake_store):
    c = TestClient(_app_with_tenants(fake_store))
    r1 = c.post("/baselines/7/promote", headers={"X-API-Key": "key-viewer"})
    assert r1.status_code == 403
    r2 = c.post("/baselines/7/promote", headers={"X-API-Key": "key-writer"})
    assert r2.status_code == 204
    assert fake_store.promotions == [7]


def test_audit_endpoints_require_admin(fake_store, tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    c = TestClient(_app_with_tenants(fake_store, audit_path=audit_path))
    # Generate one entry.
    c.post("/alerts/K/annotations",
           params={"model_id": "m", "model_version": "1.0.0"},
           headers={"X-API-Key": "key-operator"}, json={"kind": "ack"})
    # Writer cannot view audit log.
    r = c.get("/audit/verify", headers={"X-API-Key": "key-writer"})
    assert r.status_code == 403
    # Admin can.
    r = c.get("/audit/verify", headers={"X-API-Key": "key-admin"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "first_bad_seq": None, "n_entries": 1}
    r = c.get("/audit/entries", headers={"X-API-Key": "key-admin"})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_plugins_endpoint_lists_registered(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True, discover_plugins=False)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=lambda b, **k: None)
    c = TestClient(app)
    r = c.get("/plugins", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.json() == []  # no entry points in test env


def test_federate_drift_endpoint_pools_summaries(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=lambda b, **k: None)
    c = TestClient(app)
    body = {
        "summaries": [
            {"site_id": "A", "bin_edges": [0, 0.5, 1.0], "counts": [80, 20]},
            {"site_id": "B", "bin_edges": [0, 0.5, 1.0], "counts": [10, 90]},
        ],
        "warn_psi": 0.1, "alert_psi": 0.3,
    }
    r = c.post("/federate/drift", headers={"X-API-Key": "k"}, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["pooled_n"] == 200
    assert out["severity"] in {"warn", "alert"}
    assert "A" in out["per_site_psi"]


def test_federate_drift_endpoint_422_on_misaligned(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=lambda b, **k: None)
    c = TestClient(app)
    body = {"summaries": [
        {"site_id": "A", "bin_edges": [0, 0.5, 1.0], "counts": [50, 50]},
        {"site_id": "B", "bin_edges": [0, 0.3, 1.0], "counts": [50, 50]},
    ]}
    r = c.post("/federate/drift", headers={"X-API-Key": "k"}, json=body)
    assert r.status_code == 422


def test_backwards_compat_no_tenants_keeps_v04_behaviour(fake_store):
    # No tenant_keys configured → existing api_keys still grant full access.
    s = AppSettings(api_keys=["legacy"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=lambda b, **k: None)
    c = TestClient(app)
    r = c.get("/tenants/whoami", headers={"X-API-Key": "legacy"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "default"
    assert body["role"] == "admin"
