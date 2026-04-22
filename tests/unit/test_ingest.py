"""Schema and ingestion parsing tests."""

import json
from datetime import datetime, timezone

import pytest

from biomodel_monitor.ingest.loader import (
    load_batch,
    records_to_batch,
    validate_against_contract,
)
from biomodel_monitor.schema.models import (
    PredictionRecord,
    SchemaValidationError,
)


def _good_row(i: int = 0) -> dict:
    return {
        "prediction_id": f"p{i}",
        "model_id": "m",
        "model_version": "1.0",
        "timestamp": datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat(),
        "site_id": "A",
        "scanner_id": "S1",
        "stain": "H&E",
        "tissue_type": "breast",
        "cohort": "c1",
        "prediction": 1,
        "score": 0.7,
    }


def test_records_to_batch_happy_path():
    rows = [_good_row(i) for i in range(3)]
    batch = records_to_batch(rows, batch_id="b1")
    assert len(batch) == 3
    assert batch.metadata.batch_id == "b1"
    assert batch.metadata.model_id == "m"


def test_score_out_of_range_rejected():
    row = _good_row()
    row["score"] = 1.5
    with pytest.raises(SchemaValidationError):
        records_to_batch([row], batch_id="b")


def test_missing_required_field_rejected():
    row = _good_row()
    del row["site_id"]
    with pytest.raises(SchemaValidationError):
        records_to_batch([row], batch_id="b")


def test_batch_must_be_homogeneous_model_version():
    a = _good_row(0)
    b = _good_row(1)
    b["model_version"] = "2.0"
    with pytest.raises(SchemaValidationError):
        records_to_batch([a, b], batch_id="b")


def test_csv_round_trip(tmp_path):
    rows = [_good_row(i) for i in range(2)]
    p = tmp_path / "x.csv"
    import csv

    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    batch = load_batch(p)
    assert len(batch) == 2


def test_jsonl_round_trip(tmp_path):
    rows = [_good_row(i) for i in range(2)]
    p = tmp_path / "x.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    batch = load_batch(p)
    assert len(batch) == 2


def test_unsupported_format_rejected(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("nope")
    with pytest.raises(SchemaValidationError):
        load_batch(p)


def test_contract_warns_on_new_site():
    batch = records_to_batch([_good_row(0), _good_row(1)], batch_id="b")
    contract = {"expected_sites": ["B"], "model_id": "m", "model_version": "1.0"}
    warns = validate_against_contract(batch, contract)
    assert any("unregistered site" in w for w in warns)


def test_prediction_record_rejects_nan_score():
    row = _good_row()
    row["score"] = float("nan")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PredictionRecord(**row)
