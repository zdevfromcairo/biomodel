"""Persistent metrics + incident store.

A small SQLite-backed store keyed by ``(model_id, model_version, batch_id, run_id)``
that the pipeline writes to so subsequent runs can compute trends, persistence-aware
severity, and threshold auto-tuning. Stays on-prem-friendly (single file, no daemon).
"""

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
]
