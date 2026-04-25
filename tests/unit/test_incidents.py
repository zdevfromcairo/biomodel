"""Tests for the incident workspace."""

from __future__ import annotations

import pytest

from biomodel_monitor.incidents.workspace import IncidentWorkspace
from biomodel_monitor.store.repository import AlertRecord, MetricsStore


def _seed(s, model_id, model_version, *, key, severity="warn", n_runs=1):
    for i in range(n_runs):
        run = s.create_run(batch_id=f"b{i}", model_id=model_id, model_version=model_version)
        s.insert_alerts([
            AlertRecord(
                run_id=run.run_id, batch_id=f"b{i}",
                model_id=model_id, model_version=model_version,
                key=key, title=f"t-{key}", severity=severity, score=3.0,
                category="drift",
            ),
        ])


def test_summarize_open_orders_by_severity_and_persistence(tmp_path):
    s = MetricsStore(tmp_path / "s.db")
    _seed(s, "m", "1", key="recurring-warn", severity="warn", n_runs=4)
    _seed(s, "m", "1", key="oneoff-alert", severity="alert", n_runs=1)
    _seed(s, "m", "1", key="oneoff-warn", severity="warn", n_runs=1)
    ws = IncidentWorkspace(s)
    incs = ws.summarize_open(model_id="m", model_version="1")
    # alert ranks first; among warns, recurring (higher persistence) is first
    assert incs[0].key == "oneoff-alert"
    warns = [i for i in incs if i.severity == "warn"]
    assert warns[0].key == "recurring-warn"


def test_resolved_alerts_are_hidden(tmp_path):
    s = MetricsStore(tmp_path / "s.db")
    _seed(s, "m", "1", key="k1")
    ws = IncidentWorkspace(s)
    ws.acknowledge(model_id="m", model_version="1", alert_key="k1", actor="x")
    incs = ws.summarize_open(model_id="m", model_version="1")
    assert incs[0].status == "acknowledged"
    ws.resolve(model_id="m", model_version="1", alert_key="k1", actor="x")
    incs = ws.summarize_open(model_id="m", model_version="1")
    assert all(i.key != "k1" for i in incs)


def test_label_validation(tmp_path):
    s = MetricsStore(tmp_path / "s.db")
    ws = IncidentWorkspace(s)
    with pytest.raises(ValueError):
        ws.label(model_id="m", model_version="1", alert_key="k", label="bogus")
    with pytest.raises(ValueError):
        ws.comment(model_id="m", model_version="1", alert_key="k", note="   ")
