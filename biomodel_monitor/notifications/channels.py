"""Pluggable notification channels."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from biomodel_monitor.alerts.engine import Alert


class Channel(Protocol):
    name: str

    def send(self, alert: Alert, context: dict[str, Any]) -> bool: ...


@dataclass
class NotificationResult:
    channel: str
    delivered: bool
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "delivered": self.delivered,
            "error": self.error,
            "payload": self.payload,
            "attempts": self.attempts,
        }


@dataclass
class FileChannel:
    """Append alerts as JSON-Lines records to a file."""

    path: Path
    name: str = "file"

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        line = json.dumps({"alert": alert.as_dict(), "context": context}) + "\n"
        with self.path.open("a") as fh:
            fh.write(line)
        return True


def hmac_sign(body: bytes, secret: str, *, ts: str | None = None) -> dict[str, str]:
    """Return webhook headers carrying an HMAC-SHA256 signature.

    The signature covers ``ts.body`` so a recipient can validate freshness *and*
    integrity. This is the same pattern used by Stripe / GitHub webhooks.
    """
    ts = ts or str(int(time.time()))
    msg = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return {
        "X-BioModel-Timestamp": ts,
        "X-BioModel-Signature": f"v1={sig}",
    }


@dataclass
class WebhookChannel:
    """Webhook channel; HTTP delivery is delegated to ``transport``.

    ``transport(url, body, headers=None) -> int`` should return an HTTP status
    code; any 2xx is success. Older transports without the ``headers`` kwarg
    are still supported.

    If ``secret`` is set, requests are signed with HMAC-SHA256 and the signing
    headers are passed to ``transport``. If ``max_retries > 0`` the channel
    retries with exponential backoff on transport exception or non-2xx
    response.
    """

    url: str
    name: str = "webhook"
    transport: Callable[..., int] | None = None
    secret: str | None = None
    max_retries: int = 0
    backoff_base: float = 0.5
    sleep: Callable[[float], None] = time.sleep

    def _post(self, body: dict[str, Any]) -> int:
        if self.transport is None:
            raise RuntimeError(
                "WebhookChannel requires a transport callable; the core package "
                "does not depend on an HTTP client."
            )
        body_bytes = json.dumps(body, default=str).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.secret:
            headers.update(hmac_sign(body_bytes, self.secret))
        # Older transports may not accept headers; degrade gracefully.
        try:
            return int(self.transport(self.url, body, headers=headers))
        except TypeError:
            return int(self.transport(self.url, body))

    def _send_body_with_retry(self, body: dict[str, Any]) -> bool:
        attempt = 0
        last_err: Exception | None = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                status = self._post(body)
                if 200 <= status < 300:
                    return True
                last_err = RuntimeError(f"HTTP {status}")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            if attempt <= self.max_retries:
                self.sleep(self.backoff_base * (2 ** (attempt - 1)))
        if last_err:
            raise last_err
        return False

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        return self._send_body_with_retry({"alert": alert.as_dict(), "context": context})


def _slack_blocks(alert: Alert, context: dict[str, Any]) -> dict[str, Any]:
    color = {"alert": "#d50000", "warn": "#ff9800", "ok": "#43a047"}.get(alert.severity, "#607d8b")
    return {
        "attachments": [{
            "color": color,
            "title": f"[{alert.severity.upper()}] {alert.title}",
            "fields": [
                {"title": "Category", "value": alert.category, "short": True},
                {"title": "Score", "value": f"{alert.score:.2f}", "short": True},
                {"title": "Key", "value": alert.key, "short": False},
                {"title": "Hint", "value": alert.root_cause_hint or "-", "short": False},
            ],
            "footer": context.get("model_id", "biomodel-monitor"),
        }],
    }


@dataclass
class SlackChannel(WebhookChannel):
    """Slack incoming-webhook channel.

    Builds a Slack-flavoured payload and delegates HTTP delivery to ``transport``.
    """

    name: str = "slack"

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        return self._send_body_with_retry(_slack_blocks(alert, context or {}))


@dataclass
class PagerDutyChannel(WebhookChannel):
    """PagerDuty Events API v2 channel (incident creation).

    Maps alert.severity → PagerDuty severity. ``alert`` and ``warn`` create or
    update an incident; ``ok`` resolves it (PagerDuty `event_action`).
    """

    routing_key: str = ""
    name: str = "pagerduty"
    url: str = "https://events.pagerduty.com/v2/enqueue"

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        action = "resolve" if alert.severity == "ok" else "trigger"
        body = {
            "routing_key": self.routing_key,
            "event_action": action,
            "dedup_key": alert.key,
            "payload": {
                "summary": alert.title,
                "severity": {"alert": "critical", "warn": "warning", "ok": "info"}.get(
                    alert.severity, "info"
                ),
                "source": context.get("model_id", "biomodel-monitor"),
                "component": alert.category,
                "custom_details": {**alert.as_dict(), "context": context},
            },
        }
        return self._send_body_with_retry(body)


@dataclass
class TeamsChannel(WebhookChannel):
    """Microsoft Teams incoming-webhook channel (MessageCard format)."""

    name: str = "teams"

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        color = {"alert": "D50000", "warn": "FF9800", "ok": "43A047"}.get(alert.severity, "607D8B")
        body = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": color,
            "summary": alert.title,
            "title": f"[{alert.severity.upper()}] {alert.title}",
            "sections": [{
                "facts": [
                    {"name": "Category", "value": alert.category},
                    {"name": "Score", "value": f"{alert.score:.2f}"},
                    {"name": "Key", "value": alert.key},
                    {"name": "Hint", "value": alert.root_cause_hint or "-"},
                ],
                "markdown": False,
            }],
        }
        return self._send_body_with_retry(body)


@dataclass
class EmailChannel:
    """Builds a MIME message; sending is delegated to ``transport``.

    ``transport(message: EmailMessage) -> None`` should perform the actual SMTP
    submission. The default raises so the caller opts in explicitly.
    """

    sender: str
    recipients: list[str]
    name: str = "email"
    transport: Callable[[EmailMessage], None] | None = None

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        if not self.recipients:
            return False
        msg = EmailMessage()
        msg["Subject"] = f"[BioModel Monitor:{alert.severity}] {alert.title}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(
            f"Alert key:  {alert.key}\n"
            f"Severity:   {alert.severity}\n"
            f"Score:      {alert.score:.2f}\n"
            f"Category:   {alert.category}\n"
            f"Hint:       {alert.root_cause_hint or '-'}\n\n"
            f"Context:    {json.dumps(context, indent=2)}\n"
        )
        if self.transport is None:
            raise RuntimeError(
                "EmailChannel requires a transport callable for SMTP delivery."
            )
        self.transport(msg)
        return True


@dataclass
class NotificationDispatcher:
    channels: list[Channel] = field(default_factory=list)
    min_severity: str = "warn"
    severities_rank: dict[str, int] = field(
        default_factory=lambda: {"ok": 0, "warn": 1, "alert": 2}
    )

    def dispatch(
        self, alerts: Iterable[Alert], context: dict[str, Any] | None = None
    ) -> list[NotificationResult]:
        ctx = context or {}
        threshold = self.severities_rank.get(self.min_severity, 1)
        results: list[NotificationResult] = []
        for alert in alerts:
            if self.severities_rank.get(alert.severity, 0) < threshold:
                continue
            for ch in self.channels:
                try:
                    ok = ch.send(alert, ctx)
                    results.append(NotificationResult(channel=ch.name, delivered=bool(ok)))
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        NotificationResult(channel=ch.name, delivered=False, error=str(exc))
                    )
        return results
