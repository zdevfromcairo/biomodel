"""Streaming / micro-batch ingestion (v0.6).

Real medical-AI deployments don't always produce neat daily CSVs. Inference
servers stream predictions one at a time. This subpackage gives the monitor
a server-side **micro-batching window** that:

* Accepts records from many concurrent producers.
* Groups them by ``(model_id, model_version)``.
* Flushes whenever a window fills up *or* a wall-clock timeout elapses.
* Hands the resulting :class:`PredictionBatch` to the existing pipeline.

The pipeline itself is unchanged — :class:`WindowBuffer` is the only new
moving part, and it's plain stdlib (thread-safe, no external dependency).
"""

from biomodel_monitor.streaming.buffer import (
    BufferedRecord,
    WindowBuffer,
    WindowKey,
    coerce_records,
)

__all__ = [
    "BufferedRecord",
    "WindowBuffer",
    "WindowKey",
    "coerce_records",
]
