"""Tests for v0.8 server endpoints: MMD, CUSUM, events, websocket, openapi.yaml."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from biomodel_monitor.server import AppSettings, create_app
from biomodel_monitor.server.events import Event, EventBus


class _FakeStore:
    def __init__(self, *, history=None):
        self._history = history or []
        self.annotations = []
        self.promotions = []

    def metric_history(self, **kw):
        return list(self._history)

    def list_runs(self, **kw):
        return []

    def list_baselines(self, **kw):
        return []

    def add_annotation(self, ann):
        ann.id = len(self.annotations) + 1
        ann.created_at = "2026-01-01T00:00:00Z"
        self.annotations.append(ann)
        return ann

    def promote_baseline(self, bid):
        self.promotions.append(bid)

    def close(self):
        pass


def _app(store, **overrides):
    settings = AppSettings(
        require_auth=True,
        api_keys=["k1"],
        **overrides,
    )
    return create_app(settings, store_factory=lambda: store,
                      batch_runner=lambda b, **kw: None)


def test_mmd_endpoint_alt_alerts():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, (120, 3)).tolist()
    cur = rng.normal(0.7, 1, (120, 3)).tolist()
    c = TestClient(_app(_FakeStore()))
    r = c.post(
        "/mmd",
        headers={"X-API-Key": "k1"},
        json={"reference": ref, "current": cur, "n_permutations": 80},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] in ("warn", "alert")
    assert body["p_value"] is not None
    assert body["n_reference"] == 120


def test_mmd_endpoint_dim_mismatch_422():
    c = TestClient(_app(_FakeStore()))
    r = c.post(
        "/mmd",
        headers={"X-API-Key": "k1"},
        json={"reference": [[0, 0]], "current": [[1, 1, 1]], "n_permutations": 0},
    )
    assert r.status_code == 422


def test_cusum_endpoint_detects_step_in_history():
    history = [
        {"value": v, "started_at": "2026-01-01T00:00:00Z", "severity": "ok",
         "extra_json": None}
        for v in (list(np.zeros(20)) + list(np.full(20, 2.0)))
    ]
    c = TestClient(_app(_FakeStore(history=history)))
    r = c.post(
        "/cusum",
        headers={"X-API-Key": "k1"},
        json={
            "metric": "ece", "model_id": "m", "model_version": "1",
            "target": 0.0, "sigma": 1.0, "threshold": 4.0, "slack_k": 0.5,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "alert"
    assert body["direction"] == "up"
    assert body["detected_at"] is not None


def test_cusum_endpoint_no_history_404():
    c = TestClient(_app(_FakeStore(history=[])))
    r = c.post(
        "/cusum",
        headers={"X-API-Key": "k1"},
        json={"metric": "x", "model_id": "m", "model_version": "1"},
    )
    assert r.status_code == 404


def test_events_history_replays_in_order():
    c = TestClient(_app(_FakeStore()))
    # Trigger a baseline promote so the event bus records something.
    c.post("/baselines/3/promote", headers={"X-API-Key": "k1"})
    r = c.get("/events", headers={"X-API-Key": "k1"})
    assert r.status_code == 200
    types = [e["type"] for e in r.json()]
    assert "baseline.promoted" in types


def test_openapi_yaml_returns_yaml_text():
    c = TestClient(_app(_FakeStore()))
    r = c.get("/openapi.yaml", headers={"X-API-Key": "k1"})
    assert r.status_code == 200
    body = r.text
    assert "openapi:" in body
    # Sanity check that one of our v0.8 paths is in there.
    assert "/mmd" in body
    assert "/cusum" in body


def test_websocket_pushes_events_when_authorised():
    c = TestClient(_app(_FakeStore()))
    with c.websocket_connect("/ws/events?api_key=k1") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "system.info"
        # Trigger an event.
        c.post("/baselines/9/promote", headers={"X-API-Key": "k1"})
        evt = ws.receive_json()
        assert evt["type"] == "baseline.promoted"
        assert evt["payload"]["baseline_id"] == 9


def test_websocket_rejects_unauthorised():
    from starlette.websockets import WebSocketDisconnect
    c = TestClient(_app(_FakeStore()))
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/events?api_key=wrong"):
            pass


# ---------------------------------------------------------------------------
# EventBus unit-level invariants
# ---------------------------------------------------------------------------

def test_event_bus_drops_oldest_when_subscriber_full():
    bus = EventBus(max_queue=2, history=10)
    q = bus.subscribe()
    for i in range(5):
        bus.publish(Event(type="system.info", payload={"i": i}))
    # Queue capped at 2, so 3 publishes were "dropped" (oldest pushed out).
    assert q.qsize() == 2
    assert bus.dropped >= 1


def test_event_bus_history_respects_limit():
    bus = EventBus(max_queue=10, history=3)
    for i in range(5):
        bus.publish(Event(type="system.info", payload={"i": i}))
    h = bus.history()
    assert len(h) == 3
    assert [e.payload["i"] for e in h] == [2, 3, 4]


def test_event_bus_history_filter_by_type():
    bus = EventBus(history=10)
    bus.publish(Event(type="alert.emitted", payload={}))
    bus.publish(Event(type="run.completed", payload={}))
    bus.publish(Event(type="alert.emitted", payload={}))
    only_alerts = bus.history(types=["alert.emitted"])
    assert len(only_alerts) == 2
    assert all(e.type == "alert.emitted" for e in only_alerts)
