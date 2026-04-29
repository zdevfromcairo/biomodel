"""Tamper-evident, append-only audit log (v0.7).

Every mutation made through the server (annotations, baseline promotions,
tenant changes) flows through :class:`AuditLog`. Each entry stores:

* a monotonically increasing sequence number,
* an ISO-8601 timestamp,
* the actor (tenant + role + key fingerprint),
* the action and a JSON payload,
* the SHA-256 hash of the previous entry, plus the hash of *this* entry.

Because the chain commits each entry's hash into the next entry, **any
post-hoc edit invalidates every subsequent entry** — :func:`AuditLog.verify`
returns the index of the first broken link.

This is deliberately a low-tech, file-based log (JSONL) so it can be
shipped to read-only WORM storage or signed-and-notarised by an external
process; we don't pretend to do cryptographic notarisation here.
"""

from biomodel_monitor.audit.log import (
    AuditEntry,
    AuditLog,
    AuditVerifyError,
)

__all__ = ["AuditEntry", "AuditLog", "AuditVerifyError"]
