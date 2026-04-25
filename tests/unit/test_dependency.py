"""Tests for cross-model dependency graph attribution."""

from __future__ import annotations

import pytest

from biomodel_monitor.alerts.engine import Alert
from biomodel_monitor.dependency.graph import DependencyGraph, attribute_alerts


def _alert(key: str) -> Alert:
    return Alert(
        key=key, title=key, severity="warn", score=2.0, category="drift",
    )


def test_downstream_traversal_is_transitive():
    g = DependencyGraph()
    g.add("seg", "tile_clf")
    g.add("tile_clf", "slide_clf")
    assert g.downstream_of("seg") == {"tile_clf", "slide_clf"}
    assert g.upstream_of("slide_clf") == {"seg", "tile_clf"}


def test_attribute_alerts_filters_to_reachable():
    g = DependencyGraph()
    g.add("seg", "tile_clf")
    g.add("tile_clf", "slide_clf")
    alerts = [
        ("tile_clf", _alert("k1")),
        ("slide_clf", _alert("k2")),
        ("unrelated", _alert("k3")),  # not reachable from seg
    ]
    impacts = attribute_alerts(g, "seg", alerts)
    explained = {(i.downstream_model_id, tuple(i.explained_alert_keys)) for i in impacts}
    assert ("tile_clf", ("k1",)) in explained
    assert ("slide_clf", ("k2",)) in explained
    assert not any(i.downstream_model_id == "unrelated" for i in impacts)


def test_self_dependency_rejected():
    g = DependencyGraph()
    with pytest.raises(ValueError):
        g.add("a", "a")


def test_cycle_detection():
    g = DependencyGraph()
    g.add("a", "b")
    g.add("b", "c")
    assert g.detect_cycles() is False
    g.add("c", "a")
    assert g.detect_cycles() is True
