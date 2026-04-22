"""Canonical schema for a batch of model predictions.

The schema intentionally captures the metadata medical-AI monitoring needs
that generic monitoring tools usually drop on the floor: site, scanner,
stain, tissue, cohort, and (optionally hashed) demographics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

REQUIRED_FIELDS: tuple[str, ...] = (
    "prediction_id",
    "model_id",
    "model_version",
    "timestamp",
    "site_id",
    "prediction",
    "score",
)


class SchemaValidationError(ValueError):
    """Raised when a batch fails canonical schema validation."""


class PatientDemographics(BaseModel):
    """Opt-in demographics. Identifiers should be hashed upstream."""

    model_config = ConfigDict(extra="allow")

    age_band: str | None = Field(
        default=None,
        description="Coarse age band (e.g. '40-49') — never raw DOB.",
    )
    sex: str | None = None
    self_reported_ancestry: str | None = None
    patient_hash: str | None = Field(
        default=None,
        description="Stable salted hash of the patient identifier.",
    )


class CohortDescriptor(BaseModel):
    """Logical cohort grouping (study arm, population, indication, etc.)."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    expected_prevalence: float | None = Field(default=None, ge=0.0, le=1.0)


class PredictionRecord(BaseModel):
    """A single prediction with the metadata required for medical-AI monitoring."""

    model_config = ConfigDict(extra="allow")

    prediction_id: str
    model_id: str
    model_version: str
    timestamp: datetime

    site_id: str
    scanner_id: str | None = None
    stain: str | None = None
    tissue_type: str | None = None
    cohort: str | None = None

    patient_demographics: PatientDemographics | None = None

    inputs_ref: str | None = Field(
        default=None,
        description="Opaque reference (URI / hash) of the input. Never raw PHI.",
    )

    prediction: str | int | float | bool
    score: float = Field(..., ge=0.0, le=1.0)
    embedding: list[float] | None = None

    ground_truth: str | int | float | bool | None = None

    features: dict[str, float] | None = Field(
        default=None,
        description="Optional scalar features for input-drift monitoring.",
    )

    extra: dict[str, Any] | None = None

    @field_validator("score")
    @classmethod
    def _score_finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("score must be a finite number in [0, 1]")
        return float(v)


class BatchMetadata(BaseModel):
    """Top-level metadata for an ingested batch."""

    model_config = ConfigDict(extra="allow")

    batch_id: str
    model_id: str
    model_version: str
    created_at: datetime
    source: str | None = None
    notes: str | None = None


class PredictionBatch(BaseModel):
    """A batch = metadata + records, ready for monitoring."""

    model_config = ConfigDict(extra="allow")

    metadata: BatchMetadata
    records: list[PredictionRecord]

    def __len__(self) -> int:
        return len(self.records)

    def to_records(self) -> list[dict[str, Any]]:
        """Flatten records to plain dicts (handy for DataFrame conversion)."""
        return [r.model_dump(mode="json") for r in self.records]
