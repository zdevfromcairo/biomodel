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


class IngestRequest(BaseModel):
    """Push records into the server-side micro-batching window (v0.6)."""

    model_id: str
    model_version: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    flush: bool = Field(
        default=False,
        description="Force-flush this key's window after appending (e.g. end-of-day).",
    )


class IngestResponse(BaseModel):
    accepted: int
    buffered: int
    flushed_batches: int = 0
    flushed_records: int = 0
    runs_triggered: list[str] = Field(default_factory=list)


class ForecastResponse(BaseModel):
    metric: str
    severity: str
    eta_to_breach: int | None
    threshold: float | None
    direction: str
    forecast: list[dict[str, Any]]
    method: str
    notes: str | None = None


# v0.8 additions ------------------------------------------------------------

class MMDRequest(BaseModel):
    """Embedding-drift request (v0.8). Reference and current can be 1D or 2D."""

    reference: list[Any] = Field(default_factory=list)
    current: list[Any] = Field(default_factory=list)
    bandwidth: float | None = None
    n_permutations: int = 200
    warn: float = 0.05
    alert: float = 0.10


class MMDResponse(BaseModel):
    name: str
    value: float
    severity: str
    p_value: float | None = None
    bandwidth: float | None = None
    n_reference: int = 0
    n_current: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class CUSUMRequest(BaseModel):
    metric: str
    model_id: str
    model_version: str
    target: float | None = None
    sigma: float | None = None
    threshold: float = 4.0
    slack_k: float = 0.5
    limit: int = 200


class CUSUMResponse(BaseModel):
    name: str
    metric: str
    severity: str
    direction: str | None = None
    detected_at: int | None = None
    value: float
    target: float
    sigma: float
    threshold: float
    slack_k: float
    n: int
    extra: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts_ms: int
    event_id: str
    tenant_id: str | None = None


# ---------------------------------------------------------------- v0.9 ----


class ModelRecordIn(BaseModel):
    model_id: str
    model_version: str
    training_data_hash: str | None = None
    framework: str | None = None
    notes: str | None = None


class ModelRecordOut(ModelRecordIn):
    created_at: str
    status: Literal["active", "quarantined", "retired"] = "active"


class QuarantineIn(BaseModel):
    note: str | None = None


class LineageEdgeIn(BaseModel):
    upstream_model_id: str
    upstream_model_version: str
    downstream_model_id: str
    downstream_model_version: str
    kind: str = "derives_from"


class LineageEdgeOut(LineageEdgeIn):
    created_at: str


class PolicyEvalRequest(BaseModel):
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    model_id: str | None = None
    model_version: str | None = None


class PolicyActionOut(BaseModel):
    policy: str
    action: str
    message: str
    alert_key: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class WassersteinRequest(BaseModel):
    reference: list[list[float]] | list[float]
    current: list[list[float]] | list[float]
    n_projections: int = 64
    warn: float = 0.10
    alert: float = 0.25
    seed: int = 0


class WassersteinResponseOut(BaseModel):
    name: str
    value: float
    severity: str
    n_reference: int
    n_current: int
    n_projections: int
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# v0.10 — Closed loop
# --------------------------------------------------------------------------- #


class ALEnqueueItem(BaseModel):
    record_id: str
    score: float | None = None
    probs: list[float] | None = None
    strategy: str = "entropy"
    note: str | None = None


class ALEnqueueRequest(BaseModel):
    model_id: str
    model_version: str
    items: list[ALEnqueueItem]


class ALItemOut(BaseModel):
    model_id: str
    model_version: str
    record_id: str
    score: float
    strategy: str
    status: str
    label: int | str | None = None
    note: str | None = None
    created_at: str
    updated_at: str | None = None


class ALLabelIn(BaseModel):
    label: int | str
    note: str | None = None


class ConformalCalibrateRequest(BaseModel):
    probs: list[list[float]]
    labels: list[int]
    alpha: float = 0.1
    score_fn: str = "aps"


class ConformalCalibrationOut(BaseModel):
    score_fn: str
    alpha: float
    quantile: float
    n_calibration: int


class ConformalPredictRequest(BaseModel):
    probs: list[list[float]]
    calibration: ConformalCalibrationOut


class ConformalPredictOut(BaseModel):
    name: str
    value: float
    severity: str
    sets: list[list[int]]
    extra: dict[str, Any] = Field(default_factory=dict)


class ShadowMcNemarRequest(BaseModel):
    control_correct: list[int]
    canary_correct: list[int]
    alpha_warn: float = 0.05
    alpha_alert: float = 0.01


class ShadowBootstrapRequest(BaseModel):
    control: list[float]
    canary: list[float]
    n_boot: int = 2000
    seed: int = 0
    warn: float = 0.02
    alert: float = 0.05


class ShadowResponseOut(BaseModel):
    name: str
    value: float
    severity: str
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# v0.11 — Observability mesh & multi-modal
# --------------------------------------------------------------------------- #


class ModalityCheckRequest(BaseModel):
    kind: str  # "image" | "text" | "tabular"
    reference: dict[str, Any]
    current: dict[str, Any]
    warn: float = 0.10
    alert: float = 0.25


class ModalityResponseOut(BaseModel):
    name: str
    value: float
    severity: str
    kind: str
    extra: dict[str, Any] = Field(default_factory=dict)


class VectorAddRequest(BaseModel):
    namespace: str
    record_id: str
    embedding: list[float]
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorQueryRequest(BaseModel):
    namespace: str
    embedding: list[float]
    k: int = 5


class VectorNeighborOut(BaseModel):
    record_id: str
    distance: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class FingerprintRequest(BaseModel):
    canary_inputs_id: str
    predictions: list[list[float]]


class FingerprintOut(BaseModel):
    canary_inputs_id: str
    fingerprint: str
    n_predictions: int
    schema_version: int


class FingerprintCompareRequest(BaseModel):
    expected: str
    actual: str


class FingerprintCompareOut(BaseModel):
    matches: bool
    expected: str
    actual: str
    severity: str


__all__ = [
    "ALEnqueueItem",
    "ALEnqueueRequest",
    "ALItemOut",
    "ALLabelIn",
    "AlertOut",
    "AnnotationIn",
    "AnnotationOut",
    "BaselineSummary",
    "BatchSummary",
    "CUSUMRequest",
    "CUSUMResponse",
    "ChangepointResponse",
    "ConformalCalibrateRequest",
    "ConformalCalibrationOut",
    "ConformalPredictOut",
    "ConformalPredictRequest",
    "EventOut",
    "FingerprintCompareOut",
    "FingerprintCompareRequest",
    "FingerprintOut",
    "FingerprintRequest",
    "ForecastResponse",
    "HealthResponse",
    "IncidentOut",
    "IngestRequest",
    "IngestResponse",
    "LineageEdgeIn",
    "LineageEdgeOut",
    "MMDRequest",
    "MMDResponse",
    "MetricHistoryPoint",
    "ModalityCheckRequest",
    "ModalityResponseOut",
    "ModelRecordIn",
    "ModelRecordOut",
    "PolicyActionOut",
    "PolicyEvalRequest",
    "QuarantineIn",
    "RunPipelineRequest",
    "RunPipelineResponse",
    "RunSummary",
    "ShadowBootstrapRequest",
    "ShadowMcNemarRequest",
    "ShadowResponseOut",
    "VectorAddRequest",
    "VectorNeighborOut",
    "VectorQueryRequest",
    "WassersteinRequest",
    "WassersteinResponseOut",
    "WhatIfRequest",
]
