"""Notification channels for emitted alerts.

Channels ship pluggable with safe defaults: a JSON-Lines file sink that
always works on-prem, and HTTP-delegating channels (webhook, Slack,
PagerDuty, Teams) that take an injected ``transport`` so the core package
doesn't bake an HTTP library in. Webhooks support HMAC-SHA256 signing and
exponential-backoff retries (added in v0.4).
"""

from biomodel_monitor.notifications.channels import (
    Channel,
    EmailChannel,
    FileChannel,
    NotificationDispatcher,
    NotificationResult,
    PagerDutyChannel,
    SlackChannel,
    TeamsChannel,
    WebhookChannel,
    hmac_sign,
)

__all__ = [
    "Channel",
    "EmailChannel",
    "FileChannel",
    "NotificationDispatcher",
    "NotificationResult",
    "PagerDutyChannel",
    "SlackChannel",
    "TeamsChannel",
    "WebhookChannel",
    "hmac_sign",
]
