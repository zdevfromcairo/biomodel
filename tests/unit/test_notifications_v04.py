"""Tests for v0.4 notification channel additions: Slack, PagerDuty, Teams,
HMAC signing, and webhook retry logic."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from biomodel_monitor.alerts.engine import Alert
from biomodel_monitor.notifications import (
    PagerDutyChannel,
    SlackChannel,
    TeamsChannel,
    WebhookChannel,
    hmac_sign,
)


@pytest.fixture
def alert() -> Alert:
    return Alert(
        key="k1", title="Output drift", severity="alert", score=6.0,
        category="drift", root_cause_hint="check ScannerY",
    )


# ---- HMAC signing -----------------------------------------------------------
def test_hmac_sign_produces_verifiable_signature():
    body = b'{"hello":"world"}'
    secret = "shh"
    headers = hmac_sign(body, secret, ts="100")
    assert headers["X-BioModel-Timestamp"] == "100"
    assert headers["X-BioModel-Signature"].startswith("v1=")
    expected = hmac.new(secret.encode(), b"100." + body, hashlib.sha256).hexdigest()
    assert headers["X-BioModel-Signature"] == f"v1={expected}"


def test_webhook_signs_request_when_secret_set(alert):
    captured: dict[str, object] = {}

    def transport(url: str, body: dict, headers: dict | None = None) -> int:
        captured["url"] = url
        captured["headers"] = headers
        return 200

    ch = WebhookChannel(url="http://x", transport=transport, secret="s3cret")
    assert ch.send(alert, {}) is True
    assert "X-BioModel-Signature" in captured["headers"]


def test_webhook_works_with_legacy_two_arg_transport(alert):
    """Old transports without the headers kwarg must still work."""
    calls: list[tuple] = []

    def legacy_transport(url: str, body: dict) -> int:
        calls.append((url, body))
        return 200

    ch = WebhookChannel(url="http://x", transport=legacy_transport)
    assert ch.send(alert, {}) is True
    assert calls and calls[0][0] == "http://x"


# ---- retry / backoff --------------------------------------------------------
def test_webhook_retries_then_succeeds(alert):
    attempts = {"n": 0}
    sleeps: list[float] = []

    def flaky(url: str, body: dict, headers=None) -> int:
        attempts["n"] += 1
        return 500 if attempts["n"] < 3 else 200

    ch = WebhookChannel(
        url="http://x", transport=flaky, max_retries=3,
        backoff_base=0.01, sleep=sleeps.append,
    )
    assert ch.send(alert, {}) is True
    assert attempts["n"] == 3
    # exponential backoff: 0.01, 0.02 (between the 3 attempts)
    assert sleeps == [0.01, 0.02]


def test_webhook_raises_after_exhausting_retries(alert):
    def always_500(url: str, body: dict, headers=None) -> int:
        return 500

    ch = WebhookChannel(
        url="http://x", transport=always_500,
        max_retries=2, backoff_base=0.0, sleep=lambda _s: None,
    )
    with pytest.raises(RuntimeError):
        ch.send(alert, {})


# ---- Slack ------------------------------------------------------------------
def test_slack_channel_posts_blocks_payload(alert):
    captured = {}

    def transport(url: str, body: dict) -> int:
        captured["body"] = body
        return 200

    ch = SlackChannel(url="https://hooks.slack/x", transport=transport)
    assert ch.send(alert, {"model_id": "m1"}) is True
    body = captured["body"]
    assert "attachments" in body
    att = body["attachments"][0]
    assert att["color"] == "#d50000"  # alert severity
    assert any(f["title"] == "Score" for f in att["fields"])


# ---- PagerDuty --------------------------------------------------------------
def test_pagerduty_triggers_for_alert(alert):
    captured = {}

    def transport(url: str, body: dict) -> int:
        captured["body"] = body
        return 202

    ch = PagerDutyChannel(routing_key="rk-123", transport=transport)
    assert ch.send(alert, {"model_id": "m1"}) is True
    body = captured["body"]
    assert body["routing_key"] == "rk-123"
    assert body["event_action"] == "trigger"
    assert body["payload"]["severity"] == "critical"
    assert body["dedup_key"] == "k1"


def test_pagerduty_resolves_for_ok_severity():
    ok_alert = Alert(
        key="k1", title="Recovered", severity="ok", score=0.0, category="drift",
    )
    captured = {}

    def transport(url: str, body: dict) -> int:
        captured["body"] = body
        return 202

    ch = PagerDutyChannel(routing_key="rk-123", transport=transport)
    ch.send(ok_alert, {})
    assert captured["body"]["event_action"] == "resolve"


# ---- Teams ------------------------------------------------------------------
def test_teams_channel_uses_messagecard(alert):
    captured = {}

    def transport(url: str, body: dict) -> int:
        captured["body"] = body
        return 200

    ch = TeamsChannel(url="https://teams/x", transport=transport)
    assert ch.send(alert, {}) is True
    body = captured["body"]
    assert body["@type"] == "MessageCard"
    assert body["themeColor"] == "D50000"
