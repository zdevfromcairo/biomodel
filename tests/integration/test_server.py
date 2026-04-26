"""Tests for the FastAPI server (v0.4)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from biomodel_monitor import __version__
from biomodel_monitor.server import AppSettings, create_app
from biomodel_monitor.server.metrics import PrometheusRegistry


# ---- in-memory fake store ---------------------------------------------------
@dataclass
class _FakeRun:
    run_id: str
    batch_id: str
    model_id: str
    model_version: str
    started_at: str
    n_alerts: int = 0
    config_json: str | None = None


@dataclass
class _FakeAlert:
    key: str
    title: str
    severity: str
    score: float
    category: str
    root_cause_hint: str | None = None
    persistence: int = 1
    created_at: str = "2024-01-01T00:00:00"
    run_id: str = "r1"
    batch_id: str = "b1"
    model_id: str = "m"
    model_version: str = "v1"


@dataclass
class _FakeAnnotation:
    id: int | None = None
    alert_key: str = ""
    model_id: str = ""
    model_version: str = ""
    kind: str = ""
    label: str | None = None
    note: str | None = None
    actor: str | None = None
    created_at: str = "2024-01-01T00:00:00"


class FakeStore:
    def __init__(self):
        self.runs: list[_FakeRun] = []
        self.alerts: list[_FakeAlert] = []
        self.annotations: list[_FakeAnnotation] = []
        self.metrics: list[dict] = []
        self.baselines: list[dict] = []

    # ---- runs ----
    def list_runs(self, *, model_id=None, model_version=None, limit=50):
        out = [r for r in self.runs
               if (not model_id or r.model_id == model_id)
               and (not model_version or r.model_version == model_version)]
        return out[:limit]

    def create_run(self, *, batch_id, model_id, model_version, config=None):
        r = _FakeRun(
            run_id=f"r{len(self.runs)}", batch_id=batch_id,
            model_id=model_id, model_version=model_version,
            started_at="2024-01-01T00:00:00",
        )
        self.runs.append(r)
        return r

    def set_run_alerts(self, run_id, n_alerts):
        for r in self.runs:
            if r.run_id == run_id:
                r.n_alerts = n_alerts

    # ---- alerts ----
    def list_alerts(self, *, run_id=None, model_id=None, model_version=None,
                    key=None, limit=200):
        out = [a for a in self.alerts
               if (not run_id or a.run_id == run_id)
               and (not model_id or a.model_id == model_id)
               and (not model_version or a.model_version == model_version)
               and (not key or a.key == key)]
        return out[:limit]

    def insert_alerts(self, alerts):
        n = 0
        for a in alerts:
            self.alerts.append(_FakeAlert(
                key=a.key, title=a.title, severity=a.severity, score=a.score,
                category=a.category, root_cause_hint=a.root_cause_hint,
                persistence=a.persistence, created_at=a.created_at,
                run_id=a.run_id, batch_id=a.batch_id,
                model_id=a.model_id, model_version=a.model_version,
            ))
            n += 1
        return n

    def alert_persistence(self, *, model_id, model_version, key, last_n_runs=5):
        return sum(1 for a in self.alerts
                   if a.model_id == model_id and a.model_version == model_version
                   and a.key == key)

    def alert_status(self, *, model_id, model_version, key):
        for ann in reversed(self.annotations):
            if (ann.model_id == model_id and ann.model_version == model_version
                    and ann.alert_key == key):
                if ann.kind == "resolve":
                    return "resolved"
                if ann.kind == "ack":
                    return "acknowledged"
        return "open"

    def alert_label(self, *, model_id, model_version, key):
        for ann in reversed(self.annotations):
            if (ann.model_id == model_id and ann.model_version == model_version
                    and ann.alert_key == key and ann.kind == "label"):
                return ann.label
        return None

    # ---- annotations ----
    def add_annotation(self, ann):
        ann.id = len(self.annotations) + 1
        self.annotations.append(_FakeAnnotation(**ann.__dict__))
        return self.annotations[-1]

    def list_annotations(self, *, alert_key=None, model_id=None,
                         model_version=None, kind=None):
        out = self.annotations
        if alert_key:
            out = [a for a in out if a.alert_key == alert_key]
        if model_id:
            out = [a for a in out if a.model_id == model_id]
        if model_version:
            out = [a for a in out if a.model_version == model_version]
        if kind:
            out = [a for a in out if a.kind == kind]
        return out

    # ---- metric history ----
    def insert_metric(self, **kw):
        self.metrics.append(kw)

    def metric_history(self, *, model_id, model_version, name, limit=100):
        out = [m for m in self.metrics
               if m.get("model_id") == model_id
               and m.get("model_version") == model_version
               and m.get("name") == name]
        return [
            {
                "value": m.get("value"),
                "severity": m.get("severity"),
                "extra_json": None,
                "run_started_at": "2024-01-01T00:00:00",
                "created_at": "2024-01-01T00:00:00",
            }
            for m in out[:limit]
        ]

    # ---- baselines ----
    def list_baselines(self, *, model_id=None, model_version=None, status=None):
        out = self.baselines
        if model_id:
            out = [b for b in out if b["model_id"] == model_id]
        if model_version:
            out = [b for b in out if b["model_version"] == model_version]
        if status:
            out = [b for b in out if b["status"] == status]
        return out

    def upsert_batch(self, b):
        pass

    def promote_baseline(self, baseline_id):
        for b in self.baselines:
            if b["id"] == baseline_id:
                b["status"] = "promoted"

    def close(self):
        pass


# ---- fixtures ---------------------------------------------------------------
@pytest.fixture
def fake_store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def client(fake_store) -> TestClient:
    settings = AppSettings(
        store_path=":memory:", api_keys=["test-key"], require_auth=True,
    )
    app = create_app(
        settings,
        store_factory=lambda: fake_store,
        pipeline_runner=lambda *a, **k: _StubResult(),
    )
    return TestClient(app)


class _StubResult:
    def __init__(self):
        from biomodel_monitor.alerts.engine import Alert
        self.run_id = "r-stub"
        self.alerts = [Alert(
            key="k1", title="Stub alert", severity="alert", score=4.0,
            category="drift",
        )]
        self.persistence_by_key = {"k1": 1}


# ---- tests ------------------------------------------------------------------
def test_health_unauthenticated(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == __version__


def test_metrics_endpoint(client):
    r = client.get("/health")  # generate one access-log metric
    assert r.status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "biomodel_http_requests_total" in r.text


def test_auth_required(client):
    r = client.get("/runs")
    assert r.status_code == 401


def test_auth_with_key(client):
    r = client.get("/runs", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == []


def test_list_alerts(client, fake_store):
    fake_store.alerts.append(_FakeAlert(
        key="k1", title="Drift", severity="alert", score=4.0, category="drift",
        model_id="m", model_version="v1",
    ))
    r = client.get(
        "/alerts", params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["key"] == "k1"


def test_annotate_label(client, fake_store):
    r = client.post(
        "/alerts/k1/annotations",
        params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
        json={"kind": "label", "label": "tp", "actor": "alice"},
    )
    assert r.status_code == 201
    assert r.json()["label"] == "tp"
    assert fake_store.alert_label(model_id="m", model_version="v1", key="k1") == "tp"


def test_annotate_label_missing_label_400(client):
    r = client.post(
        "/alerts/k1/annotations",
        params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
        json={"kind": "label"},
    )
    assert r.status_code == 400


def test_changepoints_uses_metric_history(client, fake_store):
    series = [0.1] * 30 + [0.9] * 30
    for v in series:
        fake_store.insert_metric(
            run_id="r", batch_id="b", model_id="m", model_version="v1",
            name="psi", kind="output_score", value=v, severity="ok",
        )
    r = client.get(
        "/changepoints/psi",
        params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["n_points"] == 60
    assert body["changepoints"]


def test_trigger_run_uses_pipeline_runner(client):
    r = client.post(
        "/runs",
        headers={"X-API-Key": "test-key"},
        json={"batch_path": "/tmp/whatever.csv"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"] == "r-stub"
    assert body["n_alerts"] == 1


def test_incidents_endpoint(client, fake_store):
    fake_store.alerts.append(_FakeAlert(
        key="k1", title="Drift", severity="alert", score=4.0, category="drift",
        model_id="m", model_version="v1",
    ))
    r = client.get(
        "/incidents",
        params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    inc = r.json()
    assert inc and inc[0]["status"] == "open"


def test_model_card_endpoint(client, fake_store):
    r = client.get(
        "/model-card",
        params={"model_id": "m", "model_version": "v1"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    assert "Model Card" in r.text


def test_baselines_promote(client, fake_store):
    fake_store.baselines.append({
        "id": 1, "model_id": "m", "model_version": "v1",
        "cohort": None, "site_id": None, "status": "candidate",
        "n": 100, "created_at": "2024-01-01T00:00:00",
    })
    r = client.post(
        "/baselines/1/promote",
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 204
    assert fake_store.baselines[0]["status"] == "promoted"


# ---- prometheus registry standalone -----------------------------------------
def test_prometheus_registry_renders_counters_and_histograms():
    reg = PrometheusRegistry()
    c = reg.counter("foo_total", "test counter")
    c.inc(labels={"path": "/health"})
    c.inc(2.0, labels={"path": "/health"})
    h = reg.histogram("foo_seconds", "test hist")
    h.observe(0.01)
    h.observe(0.5)
    text = reg.render()
    assert "# TYPE foo_total counter" in text
    assert 'foo_total{path="/health"} 3.0' in text
    assert "# TYPE foo_seconds histogram" in text
    assert "foo_seconds_bucket" in text
    assert "foo_seconds_sum" in text


def test_app_settings_from_env():
    s = AppSettings.from_env({
        "BIOMODEL_API_KEYS": "a,b",
        "BIOMODEL_STORE_PATH": "/tmp/x.db",
        "BIOMODEL_PROMETHEUS": "0",
        "BIOMODEL_REQUIRE_AUTH": "0",
        "BIOMODEL_CORS": "https://app.example.com",
        "BIOMODEL_LOG_LEVEL": "DEBUG",
        "BIOMODEL_BATCH_ROOT": "/data/batches",
    })
    assert s.api_keys == ["a", "b"]
    assert s.store_path == "/tmp/x.db"
    assert s.enable_prometheus is False
    assert s.require_auth is False
    assert s.cors_origins == ["https://app.example.com"]
    assert s.log_level == "DEBUG"
    assert s.batch_root == "/data/batches"


def test_app_settings_empty_strings_use_defaults():
    """Empty env vars should not silently flip on/off semantics."""
    s = AppSettings.from_env({
        "BIOMODEL_PROMETHEUS": "",
        "BIOMODEL_REQUIRE_AUTH": "",
        "BIOMODEL_BATCH_ROOT": "",
    })
    # Both default-on; empty string falls through to default.
    assert s.enable_prometheus is True
    assert s.require_auth is True
    # Empty batch_root is treated as unset.
    assert s.batch_root is None


def test_whatif_rejects_paths_when_batch_root_unset(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True, batch_root=None)
    app = create_app(s, store_factory=lambda: fake_store)
    c = TestClient(app)
    r = c.post(
        "/whatif", headers={"X-API-Key": "k"},
        json={"batch_path": "/etc/passwd", "exclude": {}},
    )
    assert r.status_code == 400
    assert "batch_root" in r.json()["detail"]


def test_whatif_blocks_path_traversal_outside_root(fake_store, tmp_path):
    s = AppSettings(
        api_keys=["k"], require_auth=True, batch_root=str(tmp_path),
    )
    app = create_app(s, store_factory=lambda: fake_store)
    c = TestClient(app)
    # Try to escape the configured root.
    r = c.post(
        "/whatif", headers={"X-API-Key": "k"},
        json={"batch_path": "../../etc/passwd", "exclude": {}},
    )
    assert r.status_code in (400, 404)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
