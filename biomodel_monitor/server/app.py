"""FastAPI application factory for the BioModel Monitor server."""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from biomodel_monitor import __version__
from biomodel_monitor.server.metrics import PrometheusRegistry, TimerContext


@dataclass
class AppSettings:
    """Server configuration. Use ``AppSettings.from_env`` to load from env vars."""

    store_path: str = ":memory:"
    api_keys: list[str] = field(default_factory=list)
    enable_prometheus: bool = True
    log_level: str = "INFO"
    cors_origins: list[str] = field(default_factory=list)
    require_auth: bool = True
    # Filesystem root that ``/runs`` and ``/whatif`` may load batches from.
    # If unset, batch-path inputs are rejected — the operator must whitelist a
    # directory before the server can read arbitrary files.
    batch_root: str | None = None
    # Streaming buffer (v0.6).
    stream_max_records: int = 256
    stream_max_age_s: float = 30.0
    # Multi-tenancy + audit + plugins (v0.7). All optional; disabled by default
    # so v0.4–v0.6 deployments continue to work unchanged.
    tenant_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    audit_log_path: str | None = None
    discover_plugins: bool = False
    # Model registry + governance (v0.9). Optional; quarantine is enforced
    # only when a registry is configured.
    registry_path: str | None = None
    policy_path: str | None = None
    # Closed-loop active-learning queue (v0.10). Optional.
    active_learning_path: str | None = None
    # Vector embedding store for nearest-neighbor explanations (v0.11). Optional.
    vector_store_path: str | None = None

    @classmethod
    def from_env(cls, env: dict | None = None) -> "AppSettings":
        import os
        e = env if env is not None else os.environ
        keys_raw = e.get("BIOMODEL_API_KEYS", "")
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        # Treat empty strings the same as a missing variable: fall through to
        # the documented default (both flags default-on).
        prom_raw = (e.get("BIOMODEL_PROMETHEUS") or "1").strip().lower()
        auth_raw = (e.get("BIOMODEL_REQUIRE_AUTH") or "1").strip().lower()
        return cls(
            store_path=e.get("BIOMODEL_STORE_PATH", "biomodel.db"),
            api_keys=keys,
            enable_prometheus=prom_raw not in ("0", "false", "no", "off"),
            log_level=e.get("BIOMODEL_LOG_LEVEL", "INFO"),
            cors_origins=[o for o in e.get("BIOMODEL_CORS", "").split(",") if o],
            require_auth=auth_raw not in ("0", "false", "no", "off"),
            batch_root=e.get("BIOMODEL_BATCH_ROOT") or None,
            registry_path=e.get("BIOMODEL_REGISTRY_PATH") or None,
            policy_path=e.get("BIOMODEL_POLICY_PATH") or None,
            active_learning_path=e.get("BIOMODEL_ACTIVE_LEARNING_PATH") or None,
            vector_store_path=e.get("BIOMODEL_VECTOR_STORE_PATH") or None,
        )


class JsonAccessFormatter(logging.Formatter):
    """Structured JSON access log line."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        for k in ("method", "path", "status", "duration_ms", "actor", "request_id"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload, default=str)


def _configure_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("biomodel_monitor.server")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonAccessFormatter())
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


class _NoTimer:
    def __enter__(self) -> "_NoTimer":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _route_label(request: Any) -> str:
    """Return a low-cardinality label for the route (path template, not raw path)."""
    try:
        route = request.scope.get("route")
        if route is not None and hasattr(route, "path"):
            return str(route.path)
    except Exception:  # noqa: BLE001
        pass
    return str(getattr(request.url, "path", "?"))


def create_app(
    settings: AppSettings | None = None,
    *,
    store_factory: Callable[[], Any] | None = None,
    pipeline_runner: Callable[..., Any] | None = None,
    batch_runner: Callable[..., Any] | None = None,
) -> Any:
    """Build and return a FastAPI application.

    ``store_factory``, ``pipeline_runner`` and ``batch_runner`` are injection
    points; tests pass in a fake store and stub runners so the API surface
    can be exercised without disk or the full pipeline. By default the store
    is opened from ``settings.store_path``, the path-runner reads from disk
    and runs the real pipeline, and the batch-runner runs the real pipeline
    on an already-loaded :class:`PredictionBatch`.
    """
    try:
        from fastapi import (
            Body,
            Depends,
            FastAPI,
            Header,
            HTTPException,
            Query,
            Request,
            Response,
            WebSocket,
            WebSocketDisconnect,
        )
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse, PlainTextResponse
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "FastAPI is not installed. Install the server extras: "
            "`pip install -e \".[server]\"`."
        ) from e

    from biomodel_monitor.audit import AuditLog
    from biomodel_monitor.incidents.workspace import IncidentWorkspace
    from biomodel_monitor.intelligence.changepoint import detect_changepoints
    from biomodel_monitor.intelligence.modelcard import build_model_card
    from biomodel_monitor.intelligence.whatif import counterfactual_drift
    from biomodel_monitor.metrics.cusum import cusum_offline
    from biomodel_monitor.metrics.embedding_drift import mmd_rbf
    from biomodel_monitor.metrics.forecast import forecast_metric
    from biomodel_monitor.plugins import PluginRegistry, discover_plugins
    from biomodel_monitor.schema.models import PredictionRecord
    from biomodel_monitor.server.events import Event, EventBus
    from biomodel_monitor.server.schemas import (
        # v0.10
        ALEnqueueRequest,
        AlertOut,
        ALItemOut,
        ALLabelIn,
        AnnotationIn,
        AnnotationOut,
        BaselineSummary,
        BatchSummary,
        ChangepointResponse,
        ConformalCalibrateRequest,
        ConformalCalibrationOut,
        ConformalPredictOut,
        ConformalPredictRequest,
        CUSUMRequest,
        CUSUMResponse,
        EventOut,
        # v0.11
        FingerprintCompareOut,
        FingerprintCompareRequest,
        FingerprintOut,
        FingerprintRequest,
        ForecastResponse,
        HealthResponse,
        IncidentOut,
        IngestRequest,
        IngestResponse,
        LineageEdgeIn,
        LineageEdgeOut,
        MetricHistoryPoint,
        MMDRequest,
        MMDResponse,
        ModalityCheckRequest,
        ModalityResponseOut,
        ModelRecordIn,
        ModelRecordOut,
        PolicyActionOut,
        PolicyEvalRequest,
        QuarantineIn,
        RunPipelineRequest,
        RunPipelineResponse,
        RunSummary,
        ShadowBootstrapRequest,
        ShadowMcNemarRequest,
        ShadowResponseOut,
        VectorAddRequest,
        VectorNeighborOut,
        VectorQueryRequest,
        WassersteinRequest,
        WassersteinResponseOut,
        WhatIfRequest,
    )
    from biomodel_monitor.store.repository import Annotation, MetricsStore
    from biomodel_monitor.streaming import WindowBuffer, WindowKey
    from biomodel_monitor.tenancy import TenantContext, TenantRegistry

    settings = settings or AppSettings()
    logger = _configure_logging(settings.log_level)
    registry = PrometheusRegistry() if settings.enable_prometheus else None

    def _safe_batch_path(p: str) -> Path:
        """Resolve a caller-supplied batch path against the configured root.

        If ``settings.batch_root`` is unset, *no* paths are accepted — the
        server refuses to read arbitrary files. Otherwise the path is resolved
        and required to live under the root (defeats path traversal).
        """
        if not settings.batch_root:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Server is not configured to load batches from disk. "
                    "Set BIOMODEL_BATCH_ROOT (or AppSettings.batch_root) to a "
                    "whitelisted directory."
                ),
            )
        root = Path(settings.batch_root).resolve()
        try:
            resolved = (root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"invalid path: {e}") from e
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="path is outside the configured batch_root",
            ) from exc
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"no such file: {resolved}")
        return resolved

    def _default_store_factory() -> MetricsStore:
        return MetricsStore(settings.store_path)

    store_factory = store_factory or _default_store_factory

    if pipeline_runner is None:  # pragma: no cover — exercised only in real serve
        from biomodel_monitor.ingest.loader import load_batch
        from biomodel_monitor.pipeline import run_pipeline

        def _runner(batch_path: str, *, store, cohort=None, threshold=0.5, min_subgroup_n=30):
            safe = _safe_batch_path(batch_path)
            batch = load_batch(str(safe))
            return run_pipeline(
                batch, store=store, threshold=threshold, min_subgroup_n=min_subgroup_n,
            )

        pipeline_runner = _runner

    if batch_runner is None:  # pragma: no cover — exercised only in real serve
        from biomodel_monitor.pipeline import run_pipeline as _run_pipeline_real

        def _batch_runner(batch, *, store, threshold=0.5, min_subgroup_n=30):
            return _run_pipeline_real(
                batch, store=store, threshold=threshold, min_subgroup_n=min_subgroup_n,
            )

        batch_runner = _batch_runner

    # Server-side micro-batching window (v0.6).
    stream_buffer = WindowBuffer(
        max_records=settings.stream_max_records,
        max_age_s=settings.stream_max_age_s,
        source="http-ingest",
    )

    # Live event bus (v0.8): in-process pub/sub for /ws/events subscribers.
    event_bus = EventBus()

    app = FastAPI(
        title="BioModel Monitor",
        version=__version__,
        description="HTTP API for BioModel Monitor — post-deployment monitoring for medical AI.",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    request_ctr = registry.counter(
        "biomodel_http_requests_total", "API requests by status",
    ) if registry else None
    request_hist = registry.histogram(
        "biomodel_http_request_duration_seconds", "API request latency by route",
    ) if registry else None
    alert_ctr = registry.counter(
        "biomodel_alerts_emitted_total", "Alerts emitted by run, severity, category",
    ) if registry else None

    @app.middleware("http")
    async def access_log_mw(request: Request, call_next):  # noqa: ANN001
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path,
                       "status": 500, "duration_ms": duration_ms},
            )
            raise
        duration_s = time.perf_counter() - start
        duration_ms = int(duration_s * 1000)
        logger.info(
            "request",
            extra={"method": request.method, "path": request.url.path,
                   "status": response.status_code, "duration_ms": duration_ms},
        )
        if request_ctr is not None:
            request_ctr.inc(labels={"method": request.method,
                                    "status": str(response.status_code)})
        if request_hist is not None:
            request_hist.observe(duration_s, labels={"path": _route_label(request)})
        return response

    # Multi-tenancy registry (v0.7): only active if explicitly configured.
    tenant_registry: TenantRegistry | None = None
    if settings.tenant_keys:
        tenant_registry = TenantRegistry.from_mapping(settings.tenant_keys)  # type: ignore[arg-type]

    # Tamper-evident audit log (v0.7): only active if path configured.
    audit_log: AuditLog | None = None
    if settings.audit_log_path:
        audit_log = AuditLog(settings.audit_log_path)

    # Model registry + policy engine (v0.9). Both optional; without them the
    # server keeps the v0.8 behaviour unchanged.
    model_registry: ModelRegistry | None = None  # noqa: F821 — forward type
    if settings.registry_path:
        from biomodel_monitor.registry import ModelRegistry as _ModelRegistry
        model_registry = _ModelRegistry(settings.registry_path)
    policy_engine = None
    if settings.policy_path:
        from biomodel_monitor.policy import PolicyEngine as _PolicyEngine
        policy_engine = _PolicyEngine.from_yaml(Path(settings.policy_path).read_text())

    # v0.10 — closed-loop active-learning queue. Optional.
    al_queue = None
    if settings.active_learning_path:
        from biomodel_monitor.active_learning import (
            ActiveLearningQueue as _ALQueue,
        )
        al_queue = _ALQueue(settings.active_learning_path)

    # v0.11 — vector store for nearest-neighbor explanations. Optional.
    vector_store = None
    if settings.vector_store_path:
        from biomodel_monitor.vector import VectorStore as _VectorStore
        vector_store = _VectorStore(settings.vector_store_path)

    # Plugin registry (v0.7).
    plugin_registry = PluginRegistry()
    if settings.discover_plugins:
        try:
            discover_plugins(plugin_registry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("plugin discovery failed: %s", exc)

    def require_api_key(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> str:
        if not settings.require_auth:
            return x_api_key or "anonymous"
        if tenant_registry is not None:
            tc = tenant_registry.lookup(x_api_key)
            if tc is None:
                raise HTTPException(401, "Invalid or missing API key.")
            return x_api_key  # type: ignore[return-value]
        if not settings.api_keys:
            raise HTTPException(503, "Server has no API keys configured.")
        if not x_api_key or x_api_key not in settings.api_keys:
            raise HTTPException(401, "Invalid or missing API key.")
        return x_api_key

    def get_tenant(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> TenantContext:
        """Resolve the tenant context for an authenticated request (v0.7).

        Returns a synthetic *anonymous-admin* context when tenancy is not
        configured, so endpoints that ``require()`` a permission keep
        working in single-tenant deployments.
        """
        if tenant_registry is None:
            return TenantContext(tenant_id="default", role="admin",
                                 api_key_id=(x_api_key or "")[-4:])
        tc = tenant_registry.lookup(x_api_key)
        if tc is None:
            raise HTTPException(401, "Invalid or missing API key.")
        return tc

    def get_store():
        store = store_factory()
        try:
            yield store
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ routes
    @app.get("/health", response_model=HealthResponse)
    def health():
        return HealthResponse(version=__version__, store_path=settings.store_path)

    @app.get("/metrics")
    def prometheus():
        if registry is None:
            return PlainTextResponse("# prometheus disabled\n", status_code=503)
        return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs(
        store=Depends(get_store), _=Depends(require_api_key),
        model_id: str | None = None, model_version: str | None = None,
        limit: int = 50,
    ):
        runs = store.list_runs(model_id=model_id, model_version=model_version, limit=limit)
        return [RunSummary(
            run_id=r.run_id, batch_id=r.batch_id, model_id=r.model_id,
            model_version=r.model_version, started_at=r.started_at, n_alerts=r.n_alerts,
        ) for r in runs]

    @app.get("/batches", response_model=list[BatchSummary])
    def list_batches(
        store=Depends(get_store), _=Depends(require_api_key),
        model_id: str | None = None, model_version: str | None = None,
        limit: int = 50,
    ):
        runs = store.list_runs(model_id=model_id, model_version=model_version, limit=limit)
        seen, out = set(), []
        for r in runs:
            if r.batch_id in seen:
                continue
            seen.add(r.batch_id)
            out.append(BatchSummary(
                batch_id=r.batch_id, model_id=r.model_id, model_version=r.model_version,
                n_records=0, created_at=r.started_at,
            ))
        return out

    @app.get("/alerts", response_model=list[AlertOut])
    def list_alerts(
        store=Depends(get_store), _=Depends(require_api_key),
        run_id: str | None = None, model_id: str | None = None,
        model_version: str | None = None, key: str | None = None, limit: int = 100,
    ):
        rows = store.list_alerts(
            run_id=run_id, model_id=model_id,
            model_version=model_version, key=key, limit=limit,
        )
        return [AlertOut(
            key=a.key, title=a.title, severity=a.severity, score=a.score,
            category=a.category, root_cause_hint=a.root_cause_hint,
            persistence=a.persistence, created_at=a.created_at,
            run_id=a.run_id, batch_id=a.batch_id,
        ) for a in rows]

    @app.get("/incidents", response_model=list[IncidentOut])
    def list_incidents(
        model_id: str = Query(...), model_version: str = Query(...),
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        ws = IncidentWorkspace(store)
        incs = ws.summarize_open(model_id=model_id, model_version=model_version)
        return [IncidentOut(
            key=i.key, title=i.title, severity=i.severity, score=i.score,
            category=i.category, persistence=i.persistence,
            status=i.status, label=i.label,  # type: ignore[arg-type]
        ) for i in incs]

    @app.post("/alerts/{key}/annotations", response_model=AnnotationOut, status_code=201)
    def annotate(
        key: str, ann: AnnotationIn = Body(...),
        model_id: str = Query(...), model_version: str = Query(...),
        store=Depends(get_store), actor: str = Depends(require_api_key),
        tenant: TenantContext = Depends(get_tenant),
    ):
        try:
            tenant.require("annotate")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        if ann.kind == "label" and not ann.label:
            raise HTTPException(400, "label kind requires a 'label' field")
        if ann.kind == "comment" and not (ann.note and ann.note.strip()):
            raise HTTPException(400, "comment kind requires a non-empty note")
        rec = Annotation(
            alert_key=key, model_id=model_id, model_version=model_version,
            kind=ann.kind, label=ann.label, note=ann.note,
            actor=ann.actor or actor,
        )
        saved = store.add_annotation(rec)
        if audit_log is not None:
            audit_log.append(
                "annotate", {
                    "alert_key": key, "model_id": model_id,
                    "model_version": model_version, "kind": ann.kind,
                    "label": ann.label,
                },
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        event_bus.publish(Event(
            type="incident.annotated",
            tenant_id=tenant.tenant_id,
            payload={
                "alert_key": key, "model_id": model_id,
                "model_version": model_version, "kind": ann.kind,
                "label": ann.label, "actor": ann.actor or actor,
            },
        ))
        return AnnotationOut(
            id=saved.id, alert_key=saved.alert_key, model_id=saved.model_id,
            model_version=saved.model_version, kind=saved.kind, label=saved.label,
            note=saved.note, actor=saved.actor, created_at=saved.created_at,
        )

    @app.get("/metric-history", response_model=list[MetricHistoryPoint])
    def metric_history(
        model_id: str = Query(...), model_version: str = Query(...), name: str = Query(...),
        limit: int = 100, store=Depends(get_store), _=Depends(require_api_key),
    ):
        rows = store.metric_history(
            model_id=model_id, model_version=model_version, name=name, limit=limit,
        )
        out = []
        for r in rows:
            extra = json.loads(r["extra_json"]) if r.get("extra_json") else None
            out.append(MetricHistoryPoint(
                started_at=r.get("run_started_at") or r.get("created_at"),
                value=r.get("value"), severity=r.get("severity"), extra=extra,
            ))
        return out

    @app.get("/changepoints/{metric}", response_model=ChangepointResponse)
    def changepoints(
        metric: str,
        model_id: str = Query(...), model_version: str = Query(...), limit: int = 200,
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        rows = store.metric_history(
            model_id=model_id, model_version=model_version, name=metric, limit=limit,
        )
        series = [float(r["value"]) for r in rows if r.get("value") is not None]
        cps = detect_changepoints(series)
        return ChangepointResponse(
            metric=metric, n_points=len(series),
            changepoints=cps.indices, segments=cps.segments,
        )

    @app.get("/baselines", response_model=list[BaselineSummary])
    def list_baselines(
        store=Depends(get_store), _=Depends(require_api_key),
        model_id: str | None = None, model_version: str | None = None,
        status: str | None = None,
    ):
        rows = store.list_baselines(
            model_id=model_id, model_version=model_version, status=status,
        )
        return [BaselineSummary(
            id=int(r["id"]),
            model_id=r["model_id"], model_version=r["model_version"],
            cohort=r.get("cohort"), site_id=r.get("site_id"),
            status=r["status"], n=int(r.get("n", 0) or 0),
            created_at=r["created_at"], promoted_at=r.get("promoted_at"),
        ) for r in rows]

    @app.post("/baselines/{baseline_id}/promote", status_code=204)
    def promote_baseline(
        baseline_id: int,
        store=Depends(get_store), _=Depends(require_api_key),
        tenant: TenantContext = Depends(get_tenant),
    ):
        try:
            tenant.require("promote_baseline")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        store.promote_baseline(baseline_id)
        if audit_log is not None:
            audit_log.append(
                "promote_baseline", {"baseline_id": baseline_id},
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        event_bus.publish(Event(
            type="baseline.promoted",
            tenant_id=tenant.tenant_id,
            payload={"baseline_id": baseline_id},
        ))
        return Response(status_code=204)

    @app.post("/runs", response_model=RunPipelineResponse, status_code=202)
    def trigger_run(
        req: RunPipelineRequest = Body(...),
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        timer_ctx = (
            TimerContext(
                registry.histogram(
                    "biomodel_pipeline_duration_seconds", "Pipeline run latency",
                )
            )
            if registry is not None
            else _NoTimer()
        )
        try:
            with timer_ctx:
                result = pipeline_runner(
                    req.batch_path,
                    store=store, cohort=req.cohort,
                    threshold=req.threshold, min_subgroup_n=req.min_subgroup_n,
                )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        if alert_ctr is not None:
            for a in result.alerts:
                alert_ctr.inc(labels={"severity": a.severity, "category": a.category})
        for a in result.alerts:
            event_bus.publish(Event(
                type="alert.emitted",
                payload={
                    "key": a.key, "title": a.title, "severity": a.severity,
                    "score": a.score, "category": a.category,
                    "run_id": result.run_id,
                },
            ))
        event_bus.publish(Event(
            type="run.completed",
            payload={"run_id": result.run_id, "n_alerts": len(result.alerts)},
        ))
        return RunPipelineResponse(
            run_id=result.run_id, n_alerts=len(result.alerts),
            alerts=[
                AlertOut(
                    key=a.key, title=a.title, severity=a.severity, score=a.score,
                    category=a.category, root_cause_hint=a.root_cause_hint,
                    persistence=result.persistence_by_key.get(a.key, 1),
                )
                for a in result.alerts
            ],
        )

    @app.post("/ingest", response_model=IngestResponse)
    def ingest(
        req: IngestRequest = Body(...),
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        """Stream records into the per-key micro-batching window (v0.6).

        Records are appended to the in-memory window for
        ``(model_id, model_version)``. Whenever the window's size or age
        threshold is crossed the resulting :class:`PredictionBatch` is run
        through the pipeline immediately. Set ``flush=True`` to force a
        flush after this push (e.g. end-of-day).
        """
        if req.model_id != "" and req.model_version == "":
            raise HTTPException(400, "model_version is required")
        accepted = 0
        flushed: list[Any] = []
        for raw in req.records:
            try:
                rec = PredictionRecord.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"invalid record: {exc}") from exc
            batch = stream_buffer.add(req.model_id, req.model_version, rec)
            accepted += 1
            if batch is not None:
                flushed.append(batch)
        if req.flush:
            flushed.extend(stream_buffer.flush_all())
        # Also drain anything that aged out while we were appending.
        flushed.extend(stream_buffer.flush_due())
        run_ids: list[str] = []
        flushed_records = 0
        for b in flushed:
            flushed_records += len(b.records)
            try:
                result = batch_runner(b, store=store)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "batch_runner failed",
                    extra={"batch_id": b.metadata.batch_id, "error": str(exc)},
                )
                continue
            if alert_ctr is not None:
                for a in getattr(result, "alerts", []) or []:
                    alert_ctr.inc(labels={"severity": a.severity, "category": a.category})
            run_id = getattr(result, "run_id", None)
            if run_id:
                run_ids.append(run_id)
            for a in getattr(result, "alerts", []) or []:
                event_bus.publish(Event(
                    type="alert.emitted",
                    payload={
                        "key": a.key, "title": a.title,
                        "severity": a.severity, "score": a.score,
                        "category": a.category, "run_id": run_id,
                    },
                ))
        if flushed:
            event_bus.publish(Event(
                type="ingest.flushed",
                payload={
                    "model_id": req.model_id, "model_version": req.model_version,
                    "flushed_batches": len(flushed),
                    "flushed_records": flushed_records,
                    "runs_triggered": run_ids,
                },
            ))
        return IngestResponse(
            accepted=accepted,
            buffered=stream_buffer.size(WindowKey(req.model_id, req.model_version)),
            flushed_batches=len(flushed),
            flushed_records=flushed_records,
            runs_triggered=run_ids,
        )

    @app.get("/forecast", response_model=ForecastResponse)
    def forecast(
        metric: str = Query(...),
        model_id: str = Query(...),
        model_version: str = Query(...),
        horizon: int = Query(default=10, ge=1, le=200),
        threshold: float | None = Query(default=None),
        direction: Literal["above", "below"] = Query(default="above"),
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        """Forecast a metric series and project ETA to a threshold (v0.6)."""
        history = store.metric_history(
            model_id=model_id, model_version=model_version, name=metric, limit=200,
        )
        values = [float(p["value"]) for p in history if p.get("value") is not None]
        if not values:
            raise HTTPException(404, f"no history for metric '{metric}'")
        result = forecast_metric(
            values, metric_name=metric, horizon=horizon,
            threshold=threshold, direction=direction,
        )
        return ForecastResponse(
            metric=metric, severity=result.severity,
            eta_to_breach=result.eta_to_breach, threshold=result.threshold,
            direction=result.direction,
            forecast=[{"step": p.step, "value": p.value,
                       "lower": p.lower, "upper": p.upper} for p in result.forecast],
            method=result.method, notes=result.notes,
        )

    @app.post("/whatif")
    def whatif(
        req: WhatIfRequest = Body(...),
        _=Depends(require_api_key),
    ):
        from biomodel_monitor.baselines.store import Baseline
        from biomodel_monitor.ingest.loader import load_batch
        safe = _safe_batch_path(req.batch_path)
        batch = load_batch(str(safe))
        baseline = None
        if req.baseline_path:
            safe_bl = _safe_batch_path(req.baseline_path)
            data = json.loads(safe_bl.read_text())
            baseline = Baseline(**data)
        return JSONResponse(counterfactual_drift(batch, baseline, exclude=req.exclude))

    @app.get("/model-card", response_class=PlainTextResponse)
    def model_card(
        model_id: str = Query(...), model_version: str = Query(...),
        store=Depends(get_store), _=Depends(require_api_key),
    ):
        return PlainTextResponse(
            build_model_card(store, model_id=model_id, model_version=model_version)
        )

    # ---------------------------------------------------- v0.7 endpoints

    @app.get("/tenants/whoami")
    def whoami(tenant: TenantContext = Depends(get_tenant)):
        """Return the calling tenant's identity & resolved permissions (v0.7)."""
        return {
            "tenant_id": tenant.tenant_id,
            "role": tenant.role,
            "api_key_id": tenant.api_key_id,
            "permissions": [a for a in [
                "list_runs", "list_alerts", "annotate", "ingest",
                "run_pipeline", "promote_baseline", "manage_tenants",
            ] if tenant.can(a)],
        }

    @app.get("/audit/entries")
    def audit_entries(
        limit: int = Query(default=100, ge=1, le=1000),
        tenant: TenantContext = Depends(get_tenant),
    ):
        """List the most recent audit-log entries (v0.7, admin-only)."""
        try:
            tenant.require("manage_tenants")
        except Exception as exc:  # AccessDenied
            raise HTTPException(403, str(exc)) from exc
        if audit_log is None:
            raise HTTPException(503, "audit log not configured")
        entries = audit_log.entries()
        return [e.to_dict() for e in entries[-limit:]]

    @app.get("/audit/verify")
    def audit_verify(tenant: TenantContext = Depends(get_tenant)):
        """Re-walk the audit chain and report integrity (v0.7, admin-only)."""
        try:
            tenant.require("manage_tenants")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        if audit_log is None:
            raise HTTPException(503, "audit log not configured")
        ok, bad = audit_log.verify()
        return {"ok": ok, "first_bad_seq": bad,
                "n_entries": len(audit_log.entries())}

    @app.get("/plugins")
    def plugins_list(_=Depends(require_api_key)):
        """List registered plugins (v0.7)."""
        return [
            {"name": p.name, "group": p.group, "source": p.source,
             "metadata": p.metadata}
            for p in plugin_registry.list()
        ]

    @app.post("/federate/drift")
    def federate_drift(
        body: dict = Body(...), _=Depends(require_api_key),
    ):
        """Pool per-site histograms into a federated drift result (v0.7)."""
        from biomodel_monitor.federated import (
            HistogramSummary,
            aggregate_histograms,
        )
        try:
            summaries = [HistogramSummary(**s) for s in body.get("summaries", [])]
            warn = float(body.get("warn_psi", 0.10))
            alert = float(body.get("alert_psi", 0.25))
            result = aggregate_histograms(summaries, warn_psi=warn, alert_psi=alert)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return result.as_dict()

    @app.post("/federate/calibration")
    def federate_calibration(
        body: dict = Body(...), _=Depends(require_api_key),
    ):
        """Pool per-site reliability bins into a federated ECE (v0.7)."""
        from biomodel_monitor.federated import (
            CalibrationSummary,
            aggregate_calibration,
        )
        try:
            summaries = [CalibrationSummary(**s) for s in body.get("summaries", [])]
            warn = float(body.get("warn_ece", 0.05))
            alert = float(body.get("alert_ece", 0.10))
            result = aggregate_calibration(summaries, warn_ece=warn, alert_ece=alert)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return result.as_dict()

    # ---------------------------------------------------- v0.8 endpoints

    @app.post("/mmd", response_model=MMDResponse)
    def mmd(req: MMDRequest = Body(...), _=Depends(require_api_key)):
        """Embedding drift via squared MMD with an RBF kernel (v0.8)."""
        try:
            res = mmd_rbf(
                req.reference, req.current,
                bandwidth=req.bandwidth,
                n_permutations=req.n_permutations,
                warn=req.warn, alert=req.alert,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        d = res.as_dict()
        return MMDResponse(**d)

    @app.post("/cusum", response_model=CUSUMResponse)
    def cusum(req: CUSUMRequest = Body(...),
              store=Depends(get_store), _=Depends(require_api_key)):
        """Run an offline CUSUM over a stored metric history (v0.8)."""
        history = store.metric_history(
            model_id=req.model_id, model_version=req.model_version,
            name=req.metric, limit=req.limit,
        )
        values = [float(p["value"]) for p in history if p.get("value") is not None]
        if not values:
            raise HTTPException(404, f"no history for metric '{req.metric}'")
        res = cusum_offline(
            values, target=req.target, sigma=req.sigma,
            threshold=req.threshold, slack_k=req.slack_k, name="cusum",
        )
        return CUSUMResponse(metric=req.metric, **res.as_dict())

    @app.get("/events", response_model=list[EventOut])
    def events_history(
        limit: int = Query(default=100, ge=1, le=1000),
        type: str | None = Query(default=None),
        _=Depends(require_api_key),
    ):
        """Recent events from the in-process bus (v0.8).

        Useful for clients that connect *after* events fire and want a small
        replay before subscribing to /ws/events.
        """
        types = [type] if type else None
        items = event_bus.history(types=types, limit=limit)
        return [EventOut(**e.to_json()) for e in items]

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):  # noqa: ANN001 — fastapi typing
        """Live event stream (v0.8). Auth via ``?api_key=`` query string.

        Each frame is a JSON-encoded :class:`Event`. The server sends a
        ``system.info`` welcome then pushes each new event as it's published.
        """
        api_key = websocket.query_params.get("api_key") or websocket.headers.get(
            "x-api-key"
        )
        if settings.require_auth:
            authorised = False
            if tenant_registry is not None:
                authorised = tenant_registry.lookup(api_key) is not None
            else:
                authorised = bool(api_key) and api_key in settings.api_keys
            if not authorised:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        queue = event_bus.subscribe()
        try:
            await websocket.send_json(Event(
                type="system.info",
                payload={"message": "subscribed", "version": __version__},
            ).to_json())
            while True:
                evt = await queue.get()
                await websocket.send_json(evt.to_json())
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("ws_events terminated: %s", exc)
        finally:
            event_bus.unsubscribe(queue)

    @app.get("/openapi.yaml", response_class=PlainTextResponse)
    def openapi_yaml():
        """Return the OpenAPI spec as YAML (v0.8 — for SDK code generators)."""
        try:
            import yaml as _yaml
        except ImportError:  # pragma: no cover — yaml is in dependencies
            raise HTTPException(503, "PyYAML not available") from None
        return PlainTextResponse(
            _yaml.safe_dump(app.openapi(), sort_keys=False),
            media_type="application/yaml",
        )

    # ----------------------------------------------------------- v0.9 ---

    def _require_registry():
        if model_registry is None:
            raise HTTPException(
                503, "model registry not configured (set BIOMODEL_REGISTRY_PATH)"
            )
        return model_registry

    @app.get("/models", response_model=list[ModelRecordOut])
    def list_models(
        model_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        _=Depends(require_api_key),
    ):
        reg = _require_registry()
        recs = reg.list(model_id=model_id, status=status)  # type: ignore[arg-type]
        return [ModelRecordOut(**r.as_dict()) for r in recs]

    @app.get("/models/{model_id}/{model_version}", response_model=ModelRecordOut)
    def get_model(model_id: str, model_version: str,
                  _=Depends(require_api_key)):
        reg = _require_registry()
        rec = reg.get(model_id, model_version)
        if rec is None:
            raise HTTPException(404, f"unknown model {model_id} v{model_version}")
        return ModelRecordOut(**rec.as_dict())

    @app.post("/models", response_model=ModelRecordOut, status_code=201)
    def register_model(
        body: ModelRecordIn = Body(...),
        tenant: TenantContext = Depends(get_tenant),
    ):
        reg = _require_registry()
        try:
            tenant.require("register_model")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.registry import ModelRecord
        rec = reg.register(ModelRecord(**body.model_dump()))
        if audit_log is not None:
            audit_log.append(
                "register_model", body.model_dump(),
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        event_bus.publish(Event(
            type="model.registered",
            tenant_id=tenant.tenant_id,
            payload=rec.as_dict(),
        ))
        return ModelRecordOut(**rec.as_dict())

    @app.post("/models/{model_id}/{model_version}/quarantine",
              response_model=ModelRecordOut)
    def quarantine_model(
        model_id: str, model_version: str,
        body: QuarantineIn = Body(default_factory=QuarantineIn),
        tenant: TenantContext = Depends(get_tenant),
    ):
        reg = _require_registry()
        try:
            tenant.require("quarantine_model")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            rec = reg.quarantine(model_id, model_version, note=body.note)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if audit_log is not None:
            audit_log.append(
                "quarantine_model",
                {"model_id": model_id, "model_version": model_version,
                 "note": body.note},
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        event_bus.publish(Event(
            type="model.quarantined", tenant_id=tenant.tenant_id,
            payload=rec.as_dict(),
        ))
        return ModelRecordOut(**rec.as_dict())

    @app.post("/models/{model_id}/{model_version}/unquarantine",
              response_model=ModelRecordOut)
    def unquarantine_model(
        model_id: str, model_version: str,
        tenant: TenantContext = Depends(get_tenant),
    ):
        reg = _require_registry()
        try:
            tenant.require("unquarantine_model")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            rec = reg.unquarantine(model_id, model_version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if audit_log is not None:
            audit_log.append(
                "unquarantine_model",
                {"model_id": model_id, "model_version": model_version},
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        event_bus.publish(Event(
            type="model.unquarantined", tenant_id=tenant.tenant_id,
            payload=rec.as_dict(),
        ))
        return ModelRecordOut(**rec.as_dict())

    @app.post("/lineage", response_model=LineageEdgeOut, status_code=201)
    def add_lineage(
        body: LineageEdgeIn = Body(...),
        tenant: TenantContext = Depends(get_tenant),
    ):
        reg = _require_registry()
        try:
            tenant.require("add_lineage")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.registry import LineageEdge
        edge = reg.add_edge(LineageEdge(**body.model_dump()))
        return LineageEdgeOut(**edge.as_dict())

    @app.get("/lineage/{model_id}/{model_version}")
    def get_lineage(
        model_id: str, model_version: str,
        _=Depends(require_api_key),
    ):
        reg = _require_registry()
        return {
            "upstreams": [e.as_dict() for e in reg.upstreams(model_id, model_version)],
            "downstreams": [e.as_dict() for e in reg.downstreams(model_id, model_version)],
        }

    @app.post("/policy/evaluate", response_model=list[PolicyActionOut])
    def policy_evaluate(req: PolicyEvalRequest = Body(...),
                        _=Depends(require_api_key)):
        if policy_engine is None:
            raise HTTPException(
                503, "no policy engine loaded (set BIOMODEL_POLICY_PATH)"
            )
        actions = policy_engine.evaluate(
            req.alerts,
            model_id=req.model_id, model_version=req.model_version,
        )
        return [PolicyActionOut(**a.as_dict()) for a in actions]

    @app.post("/wasserstein", response_model=WassersteinResponseOut)
    def wasserstein_endpoint(req: WassersteinRequest = Body(...),
                             _=Depends(require_api_key)):
        from biomodel_monitor.metrics.wasserstein import sliced_wasserstein
        try:
            res = sliced_wasserstein(
                req.reference, req.current,
                n_projections=req.n_projections,
                warn=req.warn, alert=req.alert, seed=req.seed,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return WassersteinResponseOut(**res.as_dict())

    @app.get("/sbom")
    def sbom_endpoint(_=Depends(require_api_key)):
        from biomodel_monitor.security import build_sbom
        return build_sbom()

    # ---------------------------------------------------------------- v0.10 --
    def _require_al_queue():
        if al_queue is None:
            raise HTTPException(
                503,
                "active learning queue not configured "
                "(set BIOMODEL_ACTIVE_LEARNING_PATH)",
            )
        return al_queue

    @app.post("/active-learning/enqueue", response_model=list[ALItemOut])
    def al_enqueue(
        body: ALEnqueueRequest = Body(...),
        tenant: TenantContext = Depends(get_tenant),
    ):
        q = _require_al_queue()
        try:
            tenant.require("enqueue_label")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.active_learning import (
            QueueItem,
            score_record,
        )
        out = []
        for it in body.items:
            score = it.score
            if score is None:
                if it.probs is None:
                    raise HTTPException(
                        400, "each item must supply 'score' or 'probs'"
                    )
                try:
                    score = score_record(it.probs, strategy=it.strategy)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            qi = q.enqueue(QueueItem(
                model_id=body.model_id,
                model_version=body.model_version,
                record_id=it.record_id,
                score=float(score),
                strategy=it.strategy,
                note=it.note,
            ))
            out.append(qi)
        if audit_log is not None:
            audit_log.append(
                "al_enqueue",
                {"model_id": body.model_id,
                 "model_version": body.model_version,
                 "n": len(out)},
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        return [ALItemOut(**x.as_dict()) for x in out]

    @app.get("/active-learning/{model_id}/{model_version}/queue",
             response_model=list[ALItemOut])
    def al_next_batch(model_id: str, model_version: str, limit: int = 10,
                      _=Depends(require_api_key)):
        q = _require_al_queue()
        items = q.next_batch(model_id, model_version, limit=limit)
        return [ALItemOut(**i.as_dict()) for i in items]

    @app.get("/active-learning/{model_id}/{model_version}/stats")
    def al_stats(model_id: str, model_version: str,
                 _=Depends(require_api_key)):
        q = _require_al_queue()
        return q.stats(model_id, model_version)

    @app.post("/active-learning/{model_id}/{model_version}/{record_id}/label",
              response_model=ALItemOut)
    def al_submit_label(
        model_id: str, model_version: str, record_id: str,
        body: ALLabelIn = Body(...),
        tenant: TenantContext = Depends(get_tenant),
    ):
        q = _require_al_queue()
        try:
            tenant.require("submit_label")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            item = q.submit_label(model_id, model_version, record_id,
                                  label=body.label, note=body.note)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if audit_log is not None:
            audit_log.append(
                "al_submit_label",
                {"model_id": model_id, "model_version": model_version,
                 "record_id": record_id, "label": body.label},
                actor_tenant=tenant.tenant_id, actor_role=tenant.role,
                actor_key_fp=tenant.api_key_id,
            )
        return ALItemOut(**item.as_dict())

    @app.post("/conformal/calibrate", response_model=ConformalCalibrationOut)
    def conformal_calibrate(
        body: ConformalCalibrateRequest = Body(...),
        tenant: TenantContext = Depends(get_tenant),
    ):
        try:
            tenant.require("conformal_calibrate")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.conformal import calibrate
        try:
            cal = calibrate(body.probs, body.labels,
                            alpha=body.alpha, score_fn=body.score_fn)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return ConformalCalibrationOut(**cal.as_dict())

    @app.post("/conformal/predict", response_model=ConformalPredictOut)
    def conformal_predict(body: ConformalPredictRequest = Body(...),
                          _=Depends(require_api_key)):
        from biomodel_monitor.conformal import (
            ConformalCalibration,
            predict_sets,
        )
        cal = ConformalCalibration(**body.calibration.model_dump())
        try:
            res = predict_sets(body.probs, cal)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return ConformalPredictOut(**res.as_dict())

    @app.post("/shadow/mcnemar", response_model=ShadowResponseOut)
    def shadow_mcnemar(body: ShadowMcNemarRequest = Body(...),
                       tenant: TenantContext = Depends(get_tenant)):
        try:
            tenant.require("shadow_compare")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.shadow import mcnemar
        try:
            res = mcnemar(body.control_correct, body.canary_correct,
                          alpha_warn=body.alpha_warn,
                          alpha_alert=body.alpha_alert)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return ShadowResponseOut(**res.as_dict())

    @app.post("/shadow/bootstrap", response_model=ShadowResponseOut)
    def shadow_bootstrap(body: ShadowBootstrapRequest = Body(...),
                         tenant: TenantContext = Depends(get_tenant)):
        try:
            tenant.require("shadow_compare")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.shadow import paired_bootstrap_diff
        try:
            res = paired_bootstrap_diff(
                body.control, body.canary,
                n_boot=body.n_boot, seed=body.seed,
                warn=body.warn, alert=body.alert,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return ShadowResponseOut(**res.as_dict())

    # ---------------------------------------------------------------- v0.11 --
    @app.post("/modality/check", response_model=ModalityResponseOut)
    def modality_check(body: ModalityCheckRequest = Body(...),
                       tenant: TenantContext = Depends(get_tenant)):
        try:
            tenant.require("modality_check")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.modality import check_modality
        try:
            res = check_modality(
                body.kind, body.reference, body.current,
                warn=body.warn, alert=body.alert,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return ModalityResponseOut(**res.as_dict())

    def _require_vector_store():
        if vector_store is None:
            raise HTTPException(
                503,
                "vector store not configured (set BIOMODEL_VECTOR_STORE_PATH)",
            )
        return vector_store

    @app.post("/vector/add")
    def vector_add(body: VectorAddRequest = Body(...),
                   tenant: TenantContext = Depends(get_tenant)):
        vs = _require_vector_store()
        try:
            tenant.require("vector_add")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            vs.add(body.namespace, body.record_id,
                   body.embedding, metadata=body.metadata)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "namespace": body.namespace,
                "record_id": body.record_id}

    @app.post("/vector/query", response_model=list[VectorNeighborOut])
    def vector_query(body: VectorQueryRequest = Body(...),
                     tenant: TenantContext = Depends(get_tenant)):
        vs = _require_vector_store()
        try:
            tenant.require("vector_query")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        try:
            results = vs.query(body.namespace, body.embedding, k=body.k)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return [VectorNeighborOut(**r.as_dict()) for r in results]

    @app.post("/fingerprint", response_model=FingerprintOut)
    def fingerprint_endpoint(body: FingerprintRequest = Body(...),
                             tenant: TenantContext = Depends(get_tenant)):
        try:
            tenant.require("fingerprint_compute")
        except Exception as exc:
            raise HTTPException(403, str(exc)) from exc
        from biomodel_monitor.fingerprint import compute_fingerprint
        try:
            fp = compute_fingerprint(body.canary_inputs_id, body.predictions)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return FingerprintOut(**fp.as_dict())

    @app.post("/fingerprint/compare", response_model=FingerprintCompareOut)
    def fingerprint_compare(body: FingerprintCompareRequest = Body(...),
                            _=Depends(require_api_key)):
        from biomodel_monitor.fingerprint import compare_fingerprints
        result = compare_fingerprints(body.expected, body.actual)
        return FingerprintCompareOut(**result)

    return app


def run_uvicorn(
    settings: AppSettings | None = None, *,
    host: str = "0.0.0.0", port: int = 8080,  # noqa: S104 — explicit binding
) -> None:  # pragma: no cover — wraps uvicorn.run
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "uvicorn is not installed. Install the server extras: "
            "`pip install -e \".[server]\"`."
        ) from e
    s = settings or AppSettings()
    app = create_app(s)
    uvicorn.run(app, host=host, port=port, log_level=s.log_level.lower())


__all__ = ["AppSettings", "JsonAccessFormatter", "create_app", "run_uvicorn"]
