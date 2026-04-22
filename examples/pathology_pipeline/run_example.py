"""Generate synthetic pathology batches and run the monitor end-to-end."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline, BaselineStore
from biomodel_monitor.ingest.loader import records_to_batch
from biomodel_monitor.pipeline import run_pipeline, write_outputs


SITES = ["A", "B", "C"]
SCANNERS = {"A": "ScannerX", "B": "ScannerX", "C": "ScannerY"}
STAINS = ["H&E", "IHC"]
TISSUES = ["breast", "prostate", "colon"]
COHORT = "screening_2025"


def _make_record(
    rng: random.Random,
    i: int,
    *,
    model_id: str,
    model_version: str,
    timestamp: datetime,
    site: str,
    score_bias: float = 0.0,
    label_flip: float = 0.0,
    inject_implausible: bool = False,
) -> dict[str, Any]:
    base = rng.betavariate(2, 5) + score_bias
    score = max(0.0, min(1.0, base))
    truth = 1 if rng.random() < score else 0
    if rng.random() < label_flip:
        truth = 1 - truth
    extra: dict[str, Any] = {
        "tissue_area_fraction": min(1.0, max(0.05, rng.gauss(0.5, 0.1))),
        "field_area_mm2": 1.0,
        "mitosis_count": rng.randint(0, 20),
        "tile_scores": [
            max(0.0, min(1.0, score + rng.gauss(0, 0.05))) for _ in range(8)
        ],
    }
    if inject_implausible:
        extra["tissue_area_fraction"] = 0.10
        score = 0.95
        extra["mitosis_count"] = 500  # absurd density
        extra["markers"] = {"ER": 0.9, "TripleNegative": 0.9}
        extra["exclusive_marker_pairs"] = [["ER", "TripleNegative"]]
    return {
        "prediction_id": f"pred-{i:06d}",
        "model_id": model_id,
        "model_version": model_version,
        "timestamp": timestamp.isoformat(),
        "site_id": site,
        "scanner_id": SCANNERS[site],
        "stain": rng.choice(STAINS),
        "tissue_type": rng.choice(TISSUES),
        "cohort": COHORT,
        "prediction": int(score >= 0.5),
        "score": score,
        "ground_truth": truth,
        "features": {
            "tile_brightness": rng.gauss(0.5, 0.1),
            "tile_focus": rng.gauss(0.7, 0.05),
        },
        "extra": extra,
    }


def make_batches(seed: int = 42) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    model_id = "patho-tumor-v1"
    model_version = "1.4.0"
    t0 = datetime(2026, 4, 1, tzinfo=timezone.utc)
    reference = [
        _make_record(
            rng, i,
            model_id=model_id, model_version=model_version,
            timestamp=t0 + timedelta(minutes=i),
            site=rng.choice(SITES),
        )
        for i in range(600)
    ]
    rng2 = random.Random(seed + 1)
    current: list[dict] = []
    for i in range(600):
        site = rng2.choice(SITES)
        # Inject a site-C miscalibration and score shift.
        bias = 0.15 if site == "C" else 0.0
        flip = 0.20 if site == "C" else 0.0
        implausible = (i % 200 == 0)  # a couple of obviously implausible records
        current.append(
            _make_record(
                rng2, 1000 + i,
                model_id=model_id, model_version=model_version,
                timestamp=t0 + timedelta(days=14, minutes=i),
                site=site,
                score_bias=bias,
                label_flip=flip,
                inject_implausible=implausible,
            )
        )
    return reference, current


def run() -> None:
    here = Path(__file__).parent
    out_dir = here / "out"
    out_dir.mkdir(exist_ok=True)

    ref_rows, cur_rows = make_batches()
    ref_batch = records_to_batch(ref_rows, batch_id="reference_2026_04_01")
    cur_batch = records_to_batch(cur_rows, batch_id="current_2026_04_15")

    store = BaselineStore(out_dir / "baselines")
    baseline = Baseline.from_batch(ref_batch, cohort=COHORT)
    store.save(baseline)

    result = run_pipeline(cur_batch, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))
    paths = write_outputs(cur_batch, result, out_dir=out_dir / "reports")
    print(f"Records: {len(cur_batch)}")
    print(f"Alerts:  {len(result.alerts)}")
    for k, v in paths.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":  # pragma: no cover
    run()
