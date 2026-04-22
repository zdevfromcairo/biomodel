"""Shared pytest fixtures."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from biomodel_monitor.ingest.loader import records_to_batch
from biomodel_monitor.schema.models import PredictionBatch


def _row(i: int, *, site="A", score=None, label=None, scanner="ScannerX",
         stain="H&E", tissue="breast", cohort="c1", extra=None, features=None):
    return {
        "prediction_id": f"p{i}",
        "model_id": "m1",
        "model_version": "1.0.0",
        "timestamp": datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat(),
        "site_id": site,
        "scanner_id": scanner,
        "stain": stain,
        "tissue_type": tissue,
        "cohort": cohort,
        "prediction": int((score or 0.5) >= 0.5),
        "score": float(score if score is not None else 0.5),
        "ground_truth": label,
        "features": features or {},
        "extra": extra or {},
    }


@pytest.fixture
def make_batch():
    """Factory: build a deterministic synthetic PredictionBatch."""

    def _factory(
        n: int = 200, *, seed: int = 0, site_mix=("A", "B"), with_labels: bool = True,
        score_bias: float = 0.0,
    ) -> PredictionBatch:
        rng = random.Random(seed)
        rows = []
        for i in range(n):
            s = max(0.0, min(1.0, rng.betavariate(2, 5) + score_bias))
            y = 1 if rng.random() < s else 0
            rows.append(
                _row(
                    i,
                    site=rng.choice(list(site_mix)),
                    score=s,
                    label=y if with_labels else None,
                    features={"f1": rng.gauss(0.0, 1.0)},
                )
            )
        return records_to_batch(rows, batch_id=f"b{seed}")

    return _factory


@pytest.fixture
def row_factory():
    return _row
