"""Incident workspace operations on top of :class:`MetricsStore`."""

from __future__ import annotations

from dataclasses import dataclass, field

from biomodel_monitor.store.repository import (
    AlertRecord,
    Annotation,
    MetricsStore,
)

VALID_LABELS = {"tp", "fp", "needs_review"}


@dataclass
class IncidentSummary:
    key: str
    title: str
    severity: str
    score: float
    category: str
    persistence: int
    status: str          # open | acknowledged | resolved
    label: str | None    # tp | fp | needs_review | None
    n_runs_seen: int
    last_seen_at: str
    annotations: list[dict] = field(default_factory=list)


class IncidentWorkspace:
    """Thin façade over the store that enforces the annotation kind vocabulary."""

    def __init__(self, store: MetricsStore) -> None:
        self.store = store

    def acknowledge(
        self, *, model_id: str, model_version: str, alert_key: str,
        actor: str | None = None, note: str | None = None,
    ) -> Annotation:
        return self.store.add_annotation(
            Annotation(
                alert_key=alert_key, model_id=model_id, model_version=model_version,
                kind="ack", note=note, actor=actor,
            )
        )

    def resolve(
        self, *, model_id: str, model_version: str, alert_key: str,
        actor: str | None = None, note: str | None = None,
    ) -> Annotation:
        return self.store.add_annotation(
            Annotation(
                alert_key=alert_key, model_id=model_id, model_version=model_version,
                kind="resolve", note=note, actor=actor,
            )
        )

    def comment(
        self, *, model_id: str, model_version: str, alert_key: str,
        note: str, actor: str | None = None,
    ) -> Annotation:
        if not note.strip():
            raise ValueError("comment note must be non-empty")
        return self.store.add_annotation(
            Annotation(
                alert_key=alert_key, model_id=model_id, model_version=model_version,
                kind="comment", note=note, actor=actor,
            )
        )

    def label(
        self, *, model_id: str, model_version: str, alert_key: str,
        label: str, actor: str | None = None, note: str | None = None,
    ) -> Annotation:
        if label not in VALID_LABELS:
            raise ValueError(f"label must be one of {sorted(VALID_LABELS)}")
        return self.store.add_annotation(
            Annotation(
                alert_key=alert_key, model_id=model_id, model_version=model_version,
                kind="label", label=label, note=note, actor=actor,
            )
        )

    def summarize_open(
        self, *, model_id: str, model_version: str, last_n_runs: int = 5,
    ) -> list[IncidentSummary]:
        """Return a deduplicated, prioritized view of open incidents.

        Most recent occurrence of each alert key is kept; resolved keys are dropped;
        incidents are sorted by (severity rank, persistence desc, score desc).
        """
        alerts = self.store.list_alerts(
            model_id=model_id, model_version=model_version, limit=2000
        )
        latest: dict[str, AlertRecord] = {}
        seen_count: dict[str, int] = {}
        for a in alerts:
            seen_count[a.key] = seen_count.get(a.key, 0) + 1
            if a.key not in latest:
                latest[a.key] = a
        out: list[IncidentSummary] = []
        for key, a in latest.items():
            status = self.store.alert_status(
                model_id=model_id, model_version=model_version, key=key
            )
            if status == "resolved":
                continue
            label = self.store.alert_label(
                model_id=model_id, model_version=model_version, key=key
            )
            persistence = self.store.alert_persistence(
                model_id=model_id, model_version=model_version,
                key=key, last_n_runs=last_n_runs,
            )
            anns = [
                {
                    "kind": x.kind, "label": x.label, "note": x.note,
                    "actor": x.actor, "created_at": x.created_at,
                }
                for x in self.store.list_annotations(
                    alert_key=key, model_id=model_id, model_version=model_version
                )
            ]
            out.append(
                IncidentSummary(
                    key=key, title=a.title, severity=a.severity,
                    score=a.score, category=a.category, persistence=persistence,
                    status=status, label=label, n_runs_seen=seen_count[key],
                    last_seen_at=a.created_at, annotations=anns,
                )
            )
        sev_rank = {"alert": 0, "warn": 1, "ok": 2}
        out.sort(key=lambda s: (sev_rank.get(s.severity, 3), -s.persistence, -s.score))
        return out
