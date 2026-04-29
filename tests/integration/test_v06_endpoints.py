"""Tests for the v0.6 Python SDK and v0.6 server endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from biomodel_monitor.client import BioModelMonitorClient, HTTPError


# A "transport" the SDK uses; we record calls and return canned responses.
class FakeTransport:
    def __init__(self):
        self.calls = []
        self.responses: list[tuple[int, bytes]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": body})
        if not self.responses:
            return 200, b"{}"
        return self.responses.pop(0)


def test_sdk_health_includes_api_key_header():
    t = FakeTransport()
    t.responses.append((200, json.dumps({"status": "ok", "version": "0.6.0"}).encode()))
    c = BioModelMonitorClient("https://m.example.com", api_key="abc", transport=t)
    out = c.health()
    assert out["version"] == "0.6.0"
    assert t.calls[0]["headers"]["X-API-Key"] == "abc"
    assert t.calls[0]["url"].endswith("/health")


def test_sdk_omits_api_key_when_unset():
    t = FakeTransport()
    t.responses.append((200, b"{}"))
    c = BioModelMonitorClient("https://m.example.com", transport=t)
    c.health()
    assert "X-API-Key" not in t.calls[0]["headers"]


def test_sdk_query_params_serialise_and_drop_none():
    t = FakeTransport()
    t.responses.append((200, b"[]"))
    c = BioModelMonitorClient("https://m.example.com", transport=t)
    c.list_alerts(model_id="m1", model_version=None, limit=25)
    url = t.calls[0]["url"]
    assert "model_id=m1" in url
    assert "limit=25" in url
    assert "model_version" not in url


def test_sdk_raises_http_error_on_4xx():
    t = FakeTransport()
    t.responses.append((401, b'{"detail": "bad key"}'))
    c = BioModelMonitorClient("https://m.example.com", api_key="bad", transport=t)
    with pytest.raises(HTTPError) as excinfo:
        c.health()
    assert excinfo.value.status == 401


def test_sdk_ingest_posts_records():
    t = FakeTransport()
    t.responses.append((200, json.dumps({
        "accepted": 2, "buffered": 2, "flushed_batches": 0,
        "flushed_records": 0, "runs_triggered": [],
    }).encode()))
    c = BioModelMonitorClient("https://m.example.com", api_key="k", transport=t)
    out = c.ingest("m1", "1.0.0", [{"score": 0.1}, {"score": 0.2}], flush=True)
    assert out["accepted"] == 2
    posted = json.loads(t.calls[0]["body"])
    assert posted["model_id"] == "m1"
    assert posted["flush"] is True
    assert len(posted["records"]) == 2


# --- v0.6 server endpoint tests via TestClient ---

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from biomodel_monitor.server import AppSettings, create_app  # noqa: E402


class _FakeStore:
    """Minimal store with just what the v0.6 endpoints need."""

    def __init__(self):
        self._metric_history = []

    def metric_history(self, *, model_id, model_version, name, limit=200):
        return [p for p in self._metric_history if p["name"] == name][:limit]

    def close(self):
        pass


@pytest.fixture
def fake_store():
    return _FakeStore()


def _rec_dict(score=0.5, seq=0):
    return {
        "prediction_id": f"p{seq}",
        "model_id": "m1", "model_version": "1.0.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "site_id": "S1",
        "prediction": 1, "score": score,
    }


def test_ingest_endpoint_buffers_and_eventually_flushes(fake_store):
    runs_run = []

    def fake_batch_runner(batch, *, store, threshold=0.5, min_subgroup_n=30):
        runs_run.append(batch)
        class R:
            run_id = f"run-{len(runs_run)}"
            alerts = []
        return R()

    s = AppSettings(api_keys=["k"], require_auth=True,
                    stream_max_records=2, stream_max_age_s=60)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=fake_batch_runner)
    c = TestClient(app)

    # First record buffers; no flush yet.
    r = c.post("/ingest", headers={"X-API-Key": "k"}, json={
        "model_id": "m1", "model_version": "1.0.0",
        "records": [_rec_dict(score=0.1, seq=1)],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["buffered"] == 1
    assert body["flushed_batches"] == 0
    assert runs_run == []

    # Second record crosses size threshold (=2): flushed + run.
    r = c.post("/ingest", headers={"X-API-Key": "k"}, json={
        "model_id": "m1", "model_version": "1.0.0",
        "records": [_rec_dict(score=0.2, seq=2)],
    })
    body = r.json()
    assert body["flushed_batches"] == 1
    assert body["flushed_records"] == 2
    assert body["runs_triggered"] == ["run-1"]
    assert len(runs_run) == 1


def test_ingest_endpoint_flush_drains_window(fake_store):
    seen = []
    def runner(batch, **kw):
        seen.append(batch)
        class R:
            run_id = "x"
            alerts = []
        return R()
    s = AppSettings(api_keys=["k"], require_auth=True,
                    stream_max_records=100, stream_max_age_s=60)
    app = create_app(s, store_factory=lambda: fake_store, batch_runner=runner)
    c = TestClient(app)
    r = c.post("/ingest", headers={"X-API-Key": "k"}, json={
        "model_id": "m1", "model_version": "1.0.0",
        "records": [_rec_dict(seq=1), _rec_dict(seq=2)],
        "flush": True,
    })
    assert r.json()["flushed_batches"] == 1
    assert len(seen) == 1


def test_ingest_endpoint_rejects_invalid_record(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store,
                     batch_runner=lambda b, **k: None)
    c = TestClient(app)
    r = c.post("/ingest", headers={"X-API-Key": "k"}, json={
        "model_id": "m1", "model_version": "1.0.0",
        "records": [{"score": 99.0}],  # invalid
    })
    assert r.status_code == 422


def test_forecast_endpoint_returns_severity_and_horizon(fake_store):
    # Seed an increasing metric history.
    fake_store._metric_history = [
        {"name": "psi", "value": 0.05 + 0.02 * i, "started_at": str(i)}
        for i in range(20)
    ]
    s = AppSettings(api_keys=["k"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store)
    c = TestClient(app)
    r = c.get("/forecast", headers={"X-API-Key": "k"}, params={
        "metric": "psi", "model_id": "m1", "model_version": "1.0.0",
        "horizon": 20, "threshold": 0.6, "direction": "above",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metric"] == "psi"
    assert body["severity"] in {"warn", "alert"}
    assert len(body["forecast"]) == 20


def test_forecast_endpoint_404_on_missing_history(fake_store):
    s = AppSettings(api_keys=["k"], require_auth=True)
    app = create_app(s, store_factory=lambda: fake_store)
    c = TestClient(app)
    r = c.get("/forecast", headers={"X-API-Key": "k"}, params={
        "metric": "missing", "model_id": "m1", "model_version": "1.0.0",
    })
    assert r.status_code == 404
