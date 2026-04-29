"""Intelligence layer (v0.5).

Algorithms that don't just detect problems but help operators *understand* and
*act on* them:

* :mod:`changepoint` — detect when a drift trend started.
* :mod:`attribution` — decompose an alert into per-dimension contributions.
* :mod:`whatif` — counterfactual drift under simulated cohort exclusions.
* :mod:`active_learning` — rank open incidents by expected information gain.
* :mod:`modelcard` — generate a model-card document from store contents.
* :mod:`anomaly` — robust-z-score layered onto any metric history.
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
from biomodel_monitor.intelligence.modelcard import build_model_card
from biomodel_monitor.intelligence.whatif import counterfactual_drift

__all__ = [
    "AlertAttribution",
    "AnomalyResult",
    "ChangepointResult",
    "Interaction",
    "InteractionAttribution",
    "LabelProposal",
    "attribute_alert",
    "attribute_interactions",
    "build_model_card",
    "counterfactual_drift",
    "detect_changepoints",
    "rank_incidents_for_review",
    "robust_zscore",
]
