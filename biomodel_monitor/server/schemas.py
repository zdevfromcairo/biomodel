"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    store_path: str | None = None


class BatchSummary(BaseModel):
    batch_id: str
    model_id: str
    model_version: str
    n_records: int
    created_at: str
    source: str | None = None


class RunSummary(BaseModel):
    run_id: str
    batch_id: str
    model_id: str
    model_version: str
    started_at: str
    n_alerts: int


class AlertOut(BaseModel):
    key: str
    title: str
    severity: str
    score: float
    category: str
    root_cause_hint: str | None = None
    persistence: int = 1
    created_at: str = ""
    run_id: str = ""
    batch_id: str = ""


class AnnotationIn(BaseModel):
    kind: Literal["ack", "resolve", "comment", "label"]
    label: Literal["tp", "fp", "needs_review"] | None = None
    note: str | None = None
    actor: str | None = None


class AnnotationOut(AnnotationIn):
    id: int | None = None
    alert_key: str
    model_id: str
    model_version: str
    created_at: str


class IncidentOut(BaseModel):
    key: str
    title: str
    severity: str
    score: float
    category: str
    persistence: int
    status: Literal["open", "acknowledged", "resolved"]
    label: Literal["tp", "fp", "needs_review"] | None = None


class RunPipelineRequest(BaseModel):
    batch_path: str = Field(..., description="Filesystem path to a batch (CSV / Parquet / JSONL).")
    cohort: str | None = None
    threshold: float = 0.5
    min_subgroup_n: int = 30


class RunPipelineResponse(BaseModel):
    run_id: str | None
    n_alerts: int
    alerts: list[AlertOut] = []


class MetricHistoryPoint(BaseModel):
    started_at: str
    value: float | None
    severity: str | None = None
    extra: dict[str, Any] | None = None


class BaselineSummary(BaseModel):
    id: int
    model_id: str
    model_version: str
    cohort: str | None = None
    site_id: str | None = None
    status: str
    n: int
    created_at: str
    promoted_at: str | None = None


class ChangepointResponse(BaseModel):
    metric: str
    n_points: int
    changepoints: list[int]
    segments: list[dict[str, Any]]


class WhatIfRequest(BaseModel):
    batch_path: str
    baseline_path: str | None = None
    exclude: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Mapping dimension -> values to drop, e.g. {'scanner_id': ['ScannerY']}",
    )


__all__ = [
    "AlertOut",
    "AnnotationIn",
    "AnnotationOut",
    "BaselineSummary",
    "BatchSummary",
    "ChangepointResponse",
    "HealthResponse",
    "IncidentOut",
    "MetricHistoryPoint",
    "RunPipelineRequest",
    "RunPipelineResponse",
    "RunSummary",
    "WhatIfRequest",
]
