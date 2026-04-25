"""FastAPI application factory for the BioModel Monitor server."""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    @classmethod
    def from_env(cls, env: dict | None = None) -> "AppSettings":
        import os
        e = env if env is not None else os.environ
        keys_raw = e.get("BIOMODEL_API_KEYS", "")
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        return cls(
            store_path=e.get("BIOMODEL_STORE_PATH", "biomodel.db"),
            api_keys=keys,
            enable_prometheus=e.get("BIOMODEL_PROMETHEUS", "1") not in ("0", "false", ""),
            log_level=e.get("BIOMODEL_LOG_LEVEL", "INFO"),
            cors_origins=[o for o in e.get("BIOMODEL_CORS", "").split(",") if o],
            require_auth=e.get("BIOMODEL_REQUIRE_AUTH", "1") not in ("0", "false", ""),
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
) -> Any:
    """Build and return a FastAPI application.

    ``store_factory`` and ``pipeline_runner`` are injection points; tests pass
    in a fake store and a stub runner so the API surface can be exercised
    without disk or the full pipeline. By default the store is opened from
    ``settings.store_path`` and the runner invokes the real pipeline.
    """
    try:
        from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse, PlainTextResponse
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "FastAPI is not installed. Install the server extras: "
            "`pip install -e \".[server]\"`."
        ) from e

    from biomodel_monitor.incidents.workspace import IncidentWorkspace
    from biomodel_monitor.intelligence.changepoint import detect_changepoints
    from biomodel_monitor.intelligence.modelcard import build_model_card
    from biomodel_monitor.intelligence.whatif import counterfactual_drift
    from biomodel_monitor.server.schemas import (
        AlertOut,
        AnnotationIn,
        AnnotationOut,
        BaselineSummary,
        BatchSummary,
        ChangepointResponse,
        HealthResponse,
        IncidentOut,
        MetricHistoryPoint,
        RunPipelineRequest,
        RunPipelineResponse,
        RunSummary,
        WhatIfRequest,
    )
    from biomodel_monitor.store.repository import Annotation, MetricsStore

    settings = settings or AppSettings()
    logger = _configure_logging(settings.log_level)
    registry = PrometheusRegistry() if settings.enable_prometheus else None

    def _default_store_factory() -> MetricsStore:
        return MetricsStore(settings.store_path)

    store_factory = store_factory or _default_store_factory

    if pipeline_runner is None:  # pragma: no cover — exercised only in real serve
        from biomodel_monitor.ingest.loader import load_batch
        from biomodel_monitor.pipeline import run_pipeline

        def _runner(batch_path: str, *, store, cohort=None, threshold=0.5, min_subgroup_n=30):
            batch = load_batch(batch_path)
            return run_pipeline(
                batch, store=store, threshold=threshold, min_subgroup_n=min_subgroup_n,
            )

        pipeline_runner = _runner

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

    def require_api_key(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> str:
        if not settings.require_auth:
            return x_api_key or "anonymous"
        if not settings.api_keys:
            raise HTTPException(503, "Server has no API keys configured.")
        if not x_api_key or x_api_key not in settings.api_keys:
            raise HTTPException(401, "Invalid or missing API key.")
        return x_api_key

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
    ):
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
    ):
        store.promote_baseline(baseline_id)
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

    @app.post("/whatif")
    def whatif(
        req: WhatIfRequest = Body(...),
        _=Depends(require_api_key),
    ):
        from biomodel_monitor.baselines.store import Baseline
        from biomodel_monitor.ingest.loader import load_batch
        batch = load_batch(req.batch_path)
        baseline = None
        if req.baseline_path:
            data = json.loads(Path(req.baseline_path).read_text())
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
