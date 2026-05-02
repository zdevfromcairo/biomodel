"""Intelligence layer (v0.5+).

Algorithms that don't just detect problems but help operators *understand* and
*act on* them:

* :mod:`changepoint` — detect when a drift trend started.
* :mod:`attribution` — decompose an alert into per-dimension contributions.
* :mod:`whatif` — counterfactual drift under simulated cohort exclusions.
* :mod:`active_learning` — rank open incidents by expected information gain.
* :mod:`modelcard` — generate a model-card document from store contents.
* :mod:`anomaly` — robust-z-score layered onto any metric history.
* :mod:`causal` — pairwise interaction lift (v0.6).
* :mod:`local_attribution` — per-record leave-one-out influence (v0.8).
* :mod:`drift_graph` — directed influence graph between dimensions (v0.8).
"""

from biomodel_monitor.intelligence.active_learning import (
    LabelProposal,
    rank_incidents_for_review,
)
from biomodel_monitor.intelligence.anomaly import (
    AnomalyResult,
    robust_zscore,
)
from biomodel_monitor.intelligence.attribution import (
    AlertAttribution,
    attribute_alert,
)
from biomodel_monitor.intelligence.causal import (
    Interaction,
    InteractionAttribution,
    attribute_interactions,
)
from biomodel_monitor.intelligence.changepoint import (
    ChangepointResult,
    detect_changepoints,
)
from biomodel_monitor.intelligence.drift_graph import (
    DriftGraph,
    GraphEdge,
    GraphNode,
    build_drift_graph,
)
from biomodel_monitor.intelligence.local_attribution import (
    LocalAttribution,
    RecordInfluence,
    aggregate_top_dimensions,
    attribute_local,
)
from biomodel_monitor.intelligence.modelcard import build_model_card
from biomodel_monitor.intelligence.whatif import counterfactual_drift

__all__ = [
    "AlertAttribution",
    "AnomalyResult",
    "ChangepointResult",
    "DriftGraph",
    "GraphEdge",
    "GraphNode",
    "Interaction",
    "InteractionAttribution",
    "LabelProposal",
    "LocalAttribution",
    "RecordInfluence",
    "aggregate_top_dimensions",
    "attribute_alert",
    "attribute_interactions",
    "attribute_local",
    "build_drift_graph",
    "build_model_card",
    "counterfactual_drift",
    "detect_changepoints",
    "rank_incidents_for_review",
    "robust_zscore",
]
