"""Threshold auto-tuning from labeled historical alerts.

The product flow is: humans triage alerts and label them ``tp`` / ``fp`` /
``needs_review`` via :class:`IncidentWorkspace`. This module reads back the
score distributions of the labeled alerts and proposes new (per-category)
thresholds that minimize false-positive rate while keeping a target recall on
true positives. It is *advisory* — it only proposes; promotion to the live
config is a separate step.
"""

from __future__ import annotations

from dataclasses import dataclass

from biomodel_monitor.store.repository import MetricsStore


@dataclass
class ThresholdProposal:
    category: str
    n_tp: int
    n_fp: int
    proposed_min_score: float
    expected_tpr: float
    expected_fpr: float
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "n_tp": self.n_tp,
            "n_fp": self.n_fp,
            "proposed_min_score": self.proposed_min_score,
            "expected_tpr": self.expected_tpr,
            "expected_fpr": self.expected_fpr,
            "note": self.note,
        }


def _best_threshold(
    tp_scores: list[float], fp_scores: list[float], target_recall: float
) -> tuple[float, float, float]:
    """Pick the smallest threshold that retains >= target_recall on TPs.

    Returns (threshold, achieved_tpr, fpr_at_threshold).
    """
    if not tp_scores:
        # cannot measure recall — return median of FP scores or 0
        if fp_scores:
            t = float(sorted(fp_scores)[len(fp_scores) // 2])
            return t, 0.0, 0.5
        return 0.0, 0.0, 0.0
    candidates = sorted(set(tp_scores + fp_scores))
    best_t = candidates[0]
    best_tpr = 1.0
    best_fpr = sum(1 for s in fp_scores if s >= candidates[0]) / max(len(fp_scores), 1)
    for t in candidates:
        tpr = sum(1 for s in tp_scores if s >= t) / len(tp_scores)
        if tpr < target_recall:
            break
        fpr = sum(1 for s in fp_scores if s >= t) / max(len(fp_scores), 1)
        # prefer higher threshold (lower FPR), tie-break by tpr
        if fpr <= best_fpr + 1e-12:
            best_t, best_tpr, best_fpr = t, tpr, fpr
    return float(best_t), float(best_tpr), float(best_fpr)


def propose_thresholds(
    store: MetricsStore,
    *,
    model_id: str,
    model_version: str,
    target_recall: float = 0.9,
    min_samples_per_category: int = 5,
) -> list[ThresholdProposal]:
    """Read labeled alerts from the store and propose per-category thresholds."""
    if not 0.0 < target_recall <= 1.0:
        raise ValueError("target_recall must be in (0, 1]")

    alerts = store.list_alerts(
        model_id=model_id, model_version=model_version, limit=10000
    )
    by_cat: dict[str, dict[str, list[float]]] = {}
    for a in alerts:
        label = store.alert_label(
            model_id=model_id, model_version=model_version, key=a.key
        )
        if label not in {"tp", "fp"}:
            continue
        bucket = by_cat.setdefault(a.category, {"tp": [], "fp": []})
        bucket[label].append(float(a.score))

    proposals: list[ThresholdProposal] = []
    for cat, parts in sorted(by_cat.items()):
        tp_n, fp_n = len(parts["tp"]), len(parts["fp"])
        if tp_n + fp_n < min_samples_per_category:
            proposals.append(
                ThresholdProposal(
                    category=cat, n_tp=tp_n, n_fp=fp_n,
                    proposed_min_score=0.0,
                    expected_tpr=0.0, expected_fpr=0.0,
                    note="insufficient labels — keep current threshold",
                )
            )
            continue
        t, tpr, fpr = _best_threshold(parts["tp"], parts["fp"], target_recall)
        proposals.append(
            ThresholdProposal(
                category=cat, n_tp=tp_n, n_fp=fp_n,
                proposed_min_score=t,
                expected_tpr=tpr, expected_fpr=fpr,
                note="proposed",
            )
        )
    return proposals
