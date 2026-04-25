"""Persistent metrics + incident store.

A small SQLite-backed store keyed by ``(model_id, model_version, batch_id, run_id)``
that the pipeline writes to so subsequent runs can compute trends, persistence-aware
severity, and threshold auto-tuning. Stays on-prem-friendly (single file, no daemon).

For multi-node deployments, swap in :class:`biomodel_monitor.store.postgres.PostgresStore`,
which implements the same :class:`Storage` protocol.
"""

from biomodel_monitor.store.backend import Storage
from biomodel_monitor.store.repository import (
    AlertRecord,
    Annotation,
    BatchRecord,
    MetricsStore,
    RunRecord,
)

__all__ = [
    "Annotation",
    "AlertRecord",
    "BatchRecord",
    "MetricsStore",
    "RunRecord",
    "Storage",
]
