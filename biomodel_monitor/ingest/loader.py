"""Batch loaders for CSV, JSONL, and Parquet plus model-contract validation."""

from __future__ import annotations

import csv
import json
import warnings
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from biomodel_monitor.schema.models import (
    REQUIRED_FIELDS,
    BatchMetadata,
    PredictionBatch,
    PredictionRecord,
    SchemaValidationError,
)


def _coerce_value(key: str, value: Any) -> Any:
    """Best-effort coercion for tabular columns to match schema types."""
    if value is None or value == "":
        return None
    if key == "score":
        return float(value)
    if key == "timestamp":
        if isinstance(value, datetime):
            return value
        # accept iso string
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if key == "features" and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    if key == "embedding" and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    if key == "patient_demographics" and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _row_to_record(row: dict[str, Any]) -> PredictionRecord:
    cleaned: dict[str, Any] = {}
    for k, v in row.items():
        cleaned[k] = _coerce_value(k, v)
    missing = [f for f in REQUIRED_FIELDS if cleaned.get(f) in (None, "")]
    if missing:
        raise SchemaValidationError(
            f"Row missing required fields: {missing}. Got keys: {sorted(cleaned)}"
        )
    try:
        return PredictionRecord(**cleaned)
    except ValidationError as e:
        raise SchemaValidationError(str(e)) from e


def records_to_batch(
    rows: Iterable[dict[str, Any]],
    *,
    batch_id: str,
    model_id: str | None = None,
    model_version: str | None = None,
    source: str | None = None,
) -> PredictionBatch:
    records = [_row_to_record(r) for r in rows]
    if not records:
        raise SchemaValidationError("Cannot build a batch with zero records.")
    mid = model_id or records[0].model_id
    mver = model_version or records[0].model_version
    # All records must agree on (model_id, model_version) within a batch.
    for r in records:
        if r.model_id != mid or r.model_version != mver:
            raise SchemaValidationError(
                "All records in a batch must share the same model_id and model_version."
            )
    metadata = BatchMetadata(
        batch_id=batch_id,
        model_id=mid,
        model_version=mver,
        created_at=datetime.now(timezone.utc),
        source=source,
    )
    return PredictionBatch(metadata=metadata, records=records)


def load_csv(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    with p.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_parquet(path: str | Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Parquet support requires pyarrow. Install with 'biomodel-monitor[parquet]'."
        ) from e
    table = pq.read_table(str(path))
    return table.to_pylist()


def load_batch(
    path: str | Path,
    *,
    batch_id: str | None = None,
    fmt: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
) -> PredictionBatch:
    """Load a batch from disk. Format auto-detected from extension if not given."""
    p = Path(path)
    fmt = (fmt or p.suffix.lstrip(".")).lower()
    if fmt == "csv":
        rows = load_csv(p)
    elif fmt in ("jsonl", "ndjson"):
        rows = load_jsonl(p)
    elif fmt in ("parquet", "pq"):
        rows = load_parquet(p)
    else:
        raise SchemaValidationError(f"Unsupported batch format: {fmt!r}")
    return records_to_batch(
        rows,
        batch_id=batch_id or p.stem,
        source=str(p),
        model_id=model_id,
        model_version=model_version,
    )


def validate_against_contract(
    batch: PredictionBatch, contract: dict[str, Any]
) -> list[str]:
    """Compare a batch against a registered model contract.

    Returns a list of human-readable warnings (does not raise). The contract is
    a dict like:
        {
            "model_id": "patho-tumor-v1",
            "model_version": "1.4.0",
            "expected_features": ["tile_brightness", "tile_focus"],
            "expected_sites": ["A", "B"],
        }
    """
    warns: list[str] = []
    if "model_id" in contract and batch.metadata.model_id != contract["model_id"]:
        warns.append(
            f"model_id mismatch: batch={batch.metadata.model_id!r} contract={contract['model_id']!r}"
        )
    if (
        "model_version" in contract
        and batch.metadata.model_version != contract["model_version"]
    ):
        warns.append(
            f"model_version drift: batch={batch.metadata.model_version!r} "
            f"contract={contract['model_version']!r}"
        )
    expected_features = set(contract.get("expected_features") or [])
    if expected_features:
        seen: set[str] = set()
        for r in batch.records:
            if r.features:
                seen.update(r.features.keys())
        missing = expected_features - seen
        extra = seen - expected_features
        if missing:
            warns.append(f"features missing from batch: {sorted(missing)}")
        if extra:
            warns.append(f"unexpected features in batch: {sorted(extra)}")
    expected_sites = set(contract.get("expected_sites") or [])
    if expected_sites:
        seen_sites = {r.site_id for r in batch.records}
        new_sites = seen_sites - expected_sites
        if new_sites:
            warns.append(f"unregistered site(s) appeared in batch: {sorted(new_sites)}")
    for w in warns:
        warnings.warn(w, stacklevel=2)
    return warns
