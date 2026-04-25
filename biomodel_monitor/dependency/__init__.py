"""Cross-model dependency graph.

A model under monitoring may depend on upstream models (a tile classifier
inside a slide classifier, a segmenter inside a downstream regressor, etc.).
When the upstream model regresses, the downstream model often emits a flurry
of seemingly-unrelated alerts. This module records declared dependencies and,
given an upstream regression event, attributes downstream alerts to it.
"""

from biomodel_monitor.dependency.graph import (
    DependencyEdge,
    DependencyGraph,
    Impact,
    attribute_alerts,
)

__all__ = ["DependencyEdge", "DependencyGraph", "Impact", "attribute_alerts"]
