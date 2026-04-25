"""Notification channels for emitted alerts.

Phase 2 ships pluggable channels with safe defaults: a JSON-Lines file sink
that always works on-prem, and a webhook channel that delegates HTTP delivery
to a caller-supplied function so we don't bake a network library into the core
package. An email channel is a stub that builds the MIME message; integration
with the org's relay is left to the deployment.
"""

from biomodel_monitor.notifications.channels import (
    EmailChannel,
    FileChannel,
    NotificationDispatcher,
    NotificationResult,
    WebhookChannel,
)

__all__ = [
    "EmailChannel",
    "FileChannel",
    "NotificationDispatcher",
    "NotificationResult",
    "WebhookChannel",
]
