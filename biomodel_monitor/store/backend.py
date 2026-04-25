"""Storage backend interface.

Defines the minimum interface the pipeline + API need from a metrics store.
The default :class:`biomodel_monitor.store.repository.MetricsStore` (SQLite)
satisfies this protocol structurally. A :mod:`biomodel_monitor.store.postgres`
adapter shows how to plug a Postgres backend in without changing callers.

Why a Protocol instead of an ABC?
* Existing code already targets ``MetricsStore`` directly. By introducing a
  ``Protocol`` we get static typing benefits without forcing a runtime
  inheritance tree that would break callers.
* A future enterprise deployment can swap to Postgres / DuckDB / Snowflake
  by implementing this Protocol; the API layer never has to change.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from biomodel_monitor.store.repository import (
    AlertRecord,
    Annotation,
    BatchRecord,
    RunRecord,
)


@runtime_checkable
class Storage(Protocol):
    """Structural contract for a BioModel Monitor storage backend."""

    # ---- batches ----
    def upsert_batch(self, batch: BatchRecord) -> None: ...

    # ---- runs ----
    def create_run(
        self, *, batch_id: str, model_id: str, model_version: str,
        config: dict[str, Any] | None = None,
    ) -> RunRecord: ...

    def set_run_alerts(self, run_id: str, n_alerts: int) -> None: ...

    def list_runs(
        self, *, model_id: str | None = None, model_version: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]: ...

    # ---- alerts ----
    def insert_alerts(self, alerts: Iterable[AlertRecord]) -> int: ...

    def alert_persistence(
        self, *, model_id: str, model_version: str, key: str, last_n_runs: int = 5,
    ) -> int: ...

    def list_alerts(
        self, *, run_id: str | None = None, model_id: str | None = None,
        model_version: str | None = None, key: str | None = None, limit: int = 200,
    ) -> list[AlertRecord]: ...

    # ---- annotations ----
    def add_annotation(self, ann: Annotation) -> Annotation: ...

    def list_annotations(
        self, *, alert_key: str | None = None, model_id: str | None = None,
        model_version: str | None = None, kind: str | None = None,
    ) -> list[Annotation]: ...

    def alert_status(self, *, model_id: str, model_version: str, key: str) -> str: ...

    def alert_label(
        self, *, model_id: str, model_version: str, key: str,
    ) -> str | None: ...

    # ---- metric history ----
    def insert_metric(
        self, *, run_id: str, batch_id: str, model_id: str, model_version: str,
        name: str, kind: str, value: float | None,
        severity: str | None = None, extra: dict | None = None,
    ) -> None: ...

    def metric_history(
        self, *, model_id: str, model_version: str, name: str, limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    # ---- baselines ----
    def save_baseline(
        self, *, model_id: str, model_version: str,
        cohort: str | None, site_id: str | None,
        status: str, payload: dict,
    ) -> int: ...

    def promote_baseline(self, baseline_id: int) -> None: ...

    def get_promoted_baseline(
        self, *, model_id: str, model_version: str,
        cohort: str | None = None, site_id: str | None = None,
    ) -> dict | None: ...

    def list_baselines(
        self, *, model_id: str | None = None, model_version: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...
