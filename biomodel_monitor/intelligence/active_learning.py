"""Active learning: surface alerts most worth a human label.

Uses uncertainty + persistence + label history to rank open alerts:

* Score each open alert by *expected information gain*:
  ``H(p) * persistence_weight``
  where ``p`` is the historical TP rate for the alert's category.
* Already-labeled alerts are dropped.
* Resolved alerts are dropped.

The intent is to keep the human reviewer focused on alerts whose label will
most improve the threshold-tuning loop downstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from biomodel_monitor.incidents.workspace import IncidentWorkspace
from biomodel_monitor.store.repository import MetricsStore


@dataclass
class LabelProposal:
    alert_key: str
    title: str
    severity: str
    persistence: int
    information_gain: float
    rationale: str

    def as_dict(self) -> dict:
        return {
            "alert_key": self.alert_key, "title": self.title,
            "severity": self.severity, "persistence": self.persistence,
            "information_gain": self.information_gain, "rationale": self.rationale,
        }


def _entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def rank_incidents_for_review(
    store: MetricsStore,
    *,
    model_id: str,
    model_version: str,
    top_k: int = 10,
) -> list[LabelProposal]:
    """Return open, unlabeled alerts ordered by expected information gain."""
    ws = IncidentWorkspace(store)
    incidents = ws.summarize_open(
        model_id=model_id, model_version=model_version,
    )

    # historical TP rate per category from labels
    cat_pos: dict[str, int] = {}
    cat_tot: dict[str, int] = {}
    for cat in {i.category for i in incidents}:
        # Pull all labeled annotations for this category by inspecting alerts.
        for alert in store.list_alerts(
            model_id=model_id, model_version=model_version, limit=1000,
        ):
            if alert.category != cat:
                continue
            label = store.alert_label(
                model_id=model_id, model_version=model_version, key=alert.key,
            )
            if label not in {"tp", "fp"}:
                continue
            cat_tot[cat] = cat_tot.get(cat, 0) + 1
            if label == "tp":
                cat_pos[cat] = cat_pos.get(cat, 0) + 1

    proposals: list[LabelProposal] = []
    for inc in incidents:
        if inc.label is not None:
            continue  # already labeled
        if cat_tot.get(inc.category, 0) >= 3:
            p = cat_pos.get(inc.category, 0) / cat_tot[inc.category]
            rationale = (
                f"category '{inc.category}' has historical TP rate "
                f"{p:.2f} from {cat_tot[inc.category]} labels"
            )
        else:
            p = 0.5  # max uncertainty
            rationale = f"category '{inc.category}' has insufficient labeled history"
        gain = _entropy(p) * (1.0 + 0.25 * max(inc.persistence - 1, 0))
        proposals.append(LabelProposal(
            alert_key=inc.key, title=inc.title, severity=inc.severity,
            persistence=inc.persistence, information_gain=float(gain),
            rationale=rationale,
        ))
    proposals.sort(key=lambda p: -p.information_gain)
    return proposals[:top_k]


__all__ = ["LabelProposal", "rank_incidents_for_review"]
