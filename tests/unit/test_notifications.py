"""Tests for notification channels."""

from __future__ import annotations

import json

from biomodel_monitor.alerts.engine import Alert
from biomodel_monitor.notifications.channels import (
    FileChannel,
    NotificationDispatcher,
    WebhookChannel,
)


def _alert(severity: str = "alert") -> Alert:
    return Alert(
        key="k", title="t", severity=severity, score=4.0, category="drift",
        root_cause_hint="batch", details={"x": 1},
    )


def test_file_channel_appends_jsonl(tmp_path):
    p = tmp_path / "alerts.jsonl"
    ch = FileChannel(p)
    ch.send(_alert(), {"batch_id": "b1"})
    ch.send(_alert("warn"), {"batch_id": "b2"})
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["alert"]["key"] == "k"
    assert json.loads(lines[1])["context"]["batch_id"] == "b2"


def test_dispatcher_filters_by_min_severity(tmp_path):
    p = tmp_path / "alerts.jsonl"
    disp = NotificationDispatcher(channels=[FileChannel(p)], min_severity="alert")
    out = disp.dispatch([_alert("warn"), _alert("alert")])
    assert len(out) == 1 and out[0].delivered is True
    assert len(p.read_text().splitlines()) == 1


def test_webhook_uses_transport():
    calls = []

    def transport(url, body):
        calls.append((url, body))
        return 202

    ch = WebhookChannel(url="http://example.com/h", transport=transport)
    ok = ch.send(_alert(), {"x": 1})
    assert ok is True
    assert calls and calls[0][0] == "http://example.com/h"


def test_webhook_failure_is_captured():
    def transport(url, body):
        raise RuntimeError("connection refused")

    disp = NotificationDispatcher(
        channels=[WebhookChannel(url="http://x", transport=transport)],
        min_severity="warn",
    )
    out = disp.dispatch([_alert("alert")])
    assert out[0].delivered is False
    assert "connection refused" in (out[0].error or "")
