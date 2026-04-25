"""Pluggable notification channels."""

from __future__ import annotations

import json
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

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "delivered": self.delivered,
            "error": self.error,
            "payload": self.payload,
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


@dataclass
class WebhookChannel:
    """Webhook channel; HTTP delivery is delegated to ``transport``.

    ``transport(url, body) -> int`` should return an HTTP status code; any 2xx
    is success. The default transport raises so users opt in explicitly.
    """

    url: str
    name: str = "webhook"
    transport: Callable[[str, dict[str, Any]], int] | None = None

    def send(self, alert: Alert, context: dict[str, Any]) -> bool:
        if self.transport is None:
            raise RuntimeError(
                "WebhookChannel requires a transport callable; the core package "
                "does not depend on an HTTP client."
            )
        body = {"alert": alert.as_dict(), "context": context}
        status = int(self.transport(self.url, body))
        return 200 <= status < 300


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
