"""Concurrent batch pipeline (v0.8).

The Phase-1 :func:`biomodel_monitor.pipeline.run_pipeline` runs the four
heavy computations sequentially:

    drift  →  calibration  →  subgroups  →  silent-failure

For wide multimodal batches each of those takes hundreds of ms and they are
trivially independent. :func:`run_pipeline_async` runs them concurrently in
a :class:`~concurrent.futures.ThreadPoolExecutor`, then merges the results
and reuses the existing alert + persistence + store path for byte-identical
outputs.

Determinism: the merge order and persistence are identical to the
sequential pipeline, so the persisted run id, alert keys and severity scores
match the sync pipeline on the same input.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from biomodel_monitor.alerts.engine import AlertConfig, AlertEngine, severity_score
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.metrics.plausibility import RuleRegistry, builtin_pathology_rules
from biomodel_monitor.pipeline import (
    DEFAULT_DIMENSIONS,
    PipelineResult,
    _compute_calibration,
    _compute_drift,
    _compute_silent_failure,
    _compute_subgroups,
)
from biomodel_monitor.schema.models import PredictionBatch
from biomodel_monitor.store.repository import (
    AlertRecord,
    BatchRecord,
    MetricsStore,
)


@dataclass
class AsyncPipelineStats:
    """Per-phase wall time, exposed for tracing/observability."""

    drift_ms: int = 0
    calibration_ms: int = 0
    subgroups_ms: int = 0
    plausibility_ms: int = 0
    silent_failure_ms: int = 0
    total_ms: int = 0
    parallel: bool = True
    workers: int = 1

    def as_dict(self) -> dict:
        return {
            "drift_ms": self.drift_ms,
            "calibration_ms": self.calibration_ms,
            "subgroups_ms": self.subgroups_ms,
            "plausibility_ms": self.plausibility_ms,
            "silent_failure_ms": self.silent_failure_ms,
            "total_ms": self.total_ms,
            "parallel": self.parallel,
            "workers": self.workers,
        }


def _time_call(fn, *args, **kwargs):
    import time
    start = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, int((time.perf_counter() - start) * 1000)


def run_pipeline_async(
    batch: PredictionBatch,
    *,
    baseline: Baseline | None = None,
    rule_registry: RuleRegistry | None = None,
    alert_config: AlertConfig | None = None,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    threshold: float = 0.5,
    min_subgroup_n: int = 30,
    store: MetricsStore | None = None,
    persistence_window: int = 5,
    workers: int = 4,
) -> tuple[PipelineResult, AsyncPipelineStats]:
    """Run the pipeline with the four heavy phases scheduled concurrently.

    Returns the same :class:`PipelineResult` shape as the synchronous
    pipeline plus a :class:`AsyncPipelineStats` instance for tracing.
    """
    rule_registry = rule_registry or builtin_pathology_rules()
    engine = AlertEngine(alert_config)
    stats = AsyncPipelineStats(parallel=workers > 1, workers=max(1, workers))

    import time
    t_start = time.perf_counter()

    # Phase 1 — drift, calibration, subgroups, plausibility in parallel.
    futures: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures["drift"] = ex.submit(_time_call, _compute_drift, batch, baseline)
        futures["calibration"] = ex.submit(_time_call, _compute_calibration, batch)
        futures["subgroups"] = ex.submit(
            _time_call, _compute_subgroups, batch,
            dimensions=dimensions, threshold=threshold, min_n=min_subgroup_n,
        )
        futures["plausibility"] = ex.submit(_time_call, rule_registry.run, batch.records)
        (drift_rows, input_sev, output_sev), stats.drift_ms = futures["drift"].result()
        calibration_rows, stats.calibration_ms = futures["calibration"].result()
        subgroup_rows, stats.subgroups_ms = futures["subgroups"].result()
        plausibility, stats.plausibility_ms = futures["plausibility"].result()

    # Phase 2 — silent-failure depends on drift output, so it runs after.
    silent_rows, stats.silent_failure_ms = _time_call(
        _compute_silent_failure,
        batch, baseline, drift_rows, output_sev, input_sev,
    )

    # Mirror run_pipeline's lightweight bag-of-attributes shim so the alert
    # engine sees the exact same input shape it does in the sync path.
    class _Bag:
        def __init__(self, d: dict) -> None:
            self.__dict__.update(d)

            def as_dict(_self=self, _d=d) -> dict:
                return _d

            self.as_dict = as_dict  # type: ignore[assignment]

    alerts = []
    alerts += engine.from_drift([_Bag(r) for r in drift_rows], kind="batch")
    alerts += engine.from_calibration([_Bag(r) for r in calibration_rows])
    alerts += engine.from_subgroups([_Bag(r) for r in subgroup_rows])
    alerts += engine.from_plausibility([_Bag(v.as_dict()) for v in plausibility])
    alerts += engine.from_silent_failure([_Bag(r) for r in silent_rows])
    alerts = engine.deduplicate(alerts)

    run_id: str | None = None
    persistence_by_key: dict[str, int] = {}
    if store is not None:
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        store.upsert_batch(
            BatchRecord(
                batch_id=batch.metadata.batch_id,
                model_id=batch.metadata.model_id,
                model_version=batch.metadata.model_version,
                n_records=len(batch.records),
                created_at=_dt.now(_tz.utc).isoformat(timespec="seconds"),
                source=batch.metadata.source,
            )
        )
        run = store.create_run(
            batch_id=batch.metadata.batch_id,
            model_id=batch.metadata.model_id,
            model_version=batch.metadata.model_version,
        )
        run_id = run.run_id
        for a in alerts:
            n = store.alert_persistence(
                model_id=batch.metadata.model_id,
                model_version=batch.metadata.model_version,
                key=a.key, last_n_runs=persistence_window,
            )
            persistence_by_key[a.key] = n + 1
            a.score = severity_score(a.severity, persistence=n + 1)
        alerts.sort(key=lambda a: -a.score)
        store.insert_alerts(
            AlertRecord(
                run_id=run_id, batch_id=batch.metadata.batch_id,
                model_id=batch.metadata.model_id,
                model_version=batch.metadata.model_version,
                key=a.key, title=a.title, severity=a.severity,
                score=a.score, category=a.category,
                root_cause_hint=a.root_cause_hint,
                persistence=persistence_by_key[a.key],
                details_json=json.dumps(a.details, default=str),
            )
            for a in alerts
        )
        store.set_run_alerts(run_id, len(alerts))
        for row in drift_rows + calibration_rows + silent_rows:
            store.insert_metric(
                run_id=run_id, batch_id=batch.metadata.batch_id,
                model_id=batch.metadata.model_id,
                model_version=batch.metadata.model_version,
                name=row.get("name", row.get("kind", "metric")),
                kind=row.get("kind", "metric"),
                value=float(row["value"]) if isinstance(row.get("value"), (int, float)) else None,
                severity=row.get("severity"),
                extra={k: v for k, v in row.items() if k not in {"value", "severity"}},
            )

    stats.total_ms = int((time.perf_counter() - t_start) * 1000)

    result = PipelineResult(
        drift_results=drift_rows,
        calibration_results=calibration_rows,
        subgroup_results=subgroup_rows,
        plausibility_violations=[v.as_dict() for v in plausibility],
        silent_failure_results=silent_rows,
        alerts=alerts,
        run_id=run_id,
        persistence_by_key=persistence_by_key,
    )
    return result, stats


__all__ = ["AsyncPipelineStats", "run_pipeline_async"]
