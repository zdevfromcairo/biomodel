"""Reference-window baseline storage.

A baseline is a snapshot of distributions (scores, features, modality
metadata) for a model_id+model_version+cohort. It can be persisted to JSON
on disk for a simple, reviewable, on-prem-friendly format.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from biomodel_monitor.schema.models import PredictionBatch


@dataclass
class Baseline:
    model_id: str
    model_version: str
    cohort: str | None
    n: int
    scores: list[float] = field(default_factory=list)
    features: dict[str, list[float]] = field(default_factory=dict)
    sites: list[str] = field(default_factory=list)
    scanners: list[str] = field(default_factory=list)
    stains: list[str] = field(default_factory=list)
    tissue_types: list[str] = field(default_factory=list)

    @classmethod
    def from_batch(cls, batch: PredictionBatch, *, cohort: str | None = None) -> Baseline:
        records = batch.records
        if cohort is not None:
            records = [r for r in records if r.cohort == cohort]
        feat: dict[str, list[float]] = {}
        for r in records:
            if r.features:
                for k, v in r.features.items():
                    feat.setdefault(k, []).append(float(v))
        return cls(
            model_id=batch.metadata.model_id,
            model_version=batch.metadata.model_version,
            cohort=cohort,
            n=len(records),
            scores=[r.score for r in records],
            features=feat,
            sites=[r.site_id for r in records],
            scanners=[r.scanner_id or "" for r in records],
            stains=[r.stain or "" for r in records],
            tissue_types=[r.tissue_type or "" for r in records],
        )


class BaselineStore:
    """JSON-on-disk baseline store. One file per (model, version, cohort)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(s: str) -> str:
        return s.replace("/", "_").replace("..", "_")

    def _path(self, model_id: str, model_version: str, cohort: str | None) -> Path:
        coh = cohort or "_global"
        s = self._safe
        return self.root / f"{s(model_id)}__{s(model_version)}__{s(coh)}.json"

    def save(self, baseline: Baseline) -> Path:
        p = self._path(baseline.model_id, baseline.model_version, baseline.cohort)
        p.write_text(json.dumps(asdict(baseline), indent=2))
        return p

    def load(
        self, model_id: str, model_version: str, cohort: str | None = None
    ) -> Baseline | None:
        p = self._path(model_id, model_version, cohort)
        if not p.exists():
            return None
        data: dict[str, Any] = json.loads(p.read_text())
        return Baseline(**data)

    def list(self) -> list[Path]:
        return sorted(self.root.glob("*.json"))
