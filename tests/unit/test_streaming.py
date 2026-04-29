"""Tests for the streaming micro-batching window."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from biomodel_monitor.schema.models import PredictionRecord
from biomodel_monitor.streaming import WindowBuffer, WindowKey, coerce_records


def _rec(model_id="m1", model_version="1.0.0", score=0.5, **extra) -> PredictionRecord:
    base = dict(
        prediction_id=f"p-{score}-{extra.get('seq', 0)}",
        model_id=model_id, model_version=model_version,
        timestamp=datetime.now(tz=timezone.utc),
        site_id=extra.get("site_id", "S1"),
        prediction=1, score=score,
    )
    base.update({k: v for k, v in extra.items() if k not in {"seq"}})
    return PredictionRecord(**base)


def test_buffer_flushes_on_size():
    buf = WindowBuffer(max_records=3, max_age_s=60)
    out1 = buf.add("m1", "1.0.0", _rec(seq=1))
    out2 = buf.add("m1", "1.0.0", _rec(seq=2))
    assert out1 is None and out2 is None
    out3 = buf.add("m1", "1.0.0", _rec(seq=3))
    assert out3 is not None
    assert len(out3) == 3
    assert out3.metadata.model_id == "m1"
    assert out3.metadata.model_version == "1.0.0"
    assert "size" in out3.metadata.notes
    # Window should be empty after flush.
    assert buf.size(WindowKey("m1", "1.0.0")) == 0


def test_buffer_flushes_on_age():
    buf = WindowBuffer(max_records=100, max_age_s=10)
    buf.add("m1", "1.0.0", _rec(seq=1), now=1000.0)
    buf.add("m1", "1.0.0", _rec(seq=2), now=1001.0)
    # Not yet aged out.
    assert buf.flush_due(now=1005.0) == []
    # Force the age clock past the threshold.
    flushed = buf.flush_due(now=1011.0)
    assert len(flushed) == 1
    assert len(flushed[0]) == 2
    assert "age" in flushed[0].metadata.notes


def test_buffer_isolates_keys():
    buf = WindowBuffer(max_records=2, max_age_s=60)
    assert buf.add("m1", "1.0.0", _rec()) is None
    assert buf.add("m2", "1.0.0", _rec(model_id="m2")) is None
    out = buf.add("m1", "1.0.0", _rec())
    assert out is not None and out.metadata.model_id == "m1"
    # m2 still has its single record.
    assert buf.size(WindowKey("m2", "1.0.0")) == 1


def test_buffer_flush_all_drains_every_key():
    buf = WindowBuffer(max_records=100, max_age_s=60)
    buf.add("m1", "1.0.0", _rec())
    buf.add("m2", "1.0.0", _rec(model_id="m2"))
    flushed = buf.flush_all()
    assert {b.metadata.model_id for b in flushed} == {"m1", "m2"}
    assert buf.size() == 0


def test_buffer_validates_arguments():
    with pytest.raises(ValueError):
        WindowBuffer(max_records=0)
    with pytest.raises(ValueError):
        WindowBuffer(max_age_s=0)
    buf = WindowBuffer()
    with pytest.raises(TypeError):
        buf.add("m1", "1.0.0", {"score": 0.5})  # type: ignore[arg-type]


def test_coerce_records_validates_dicts():
    out = coerce_records([
        {
            "prediction_id": "p1", "model_id": "m1", "model_version": "1.0.0",
            "timestamp": "2026-04-01T00:00:00Z", "site_id": "S",
            "prediction": 1, "score": 0.7,
        }
    ])
    assert len(out) == 1 and isinstance(out[0], PredictionRecord)
    with pytest.raises(Exception):  # noqa: B017 — pydantic raises ValidationError
        coerce_records([{"score": 2.0}])
