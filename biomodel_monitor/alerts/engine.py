"""Alert engine: turn metric & violation results into deduplicated, scored alerts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

SEVERITY_RANK = {"ok": 0, "warn": 1, "alert": 2}


def severity_score(
    metric_severity: str,
    *,
    subgroup_importance: float = 1.0,
    persistence: int = 1,
) -> float:
    """Combine metric severity, subgroup importance, and run-persistence.

    Returns a value in [0, 10].
    """
    base = SEVERITY_RANK.get(metric_severity, 0)
    raw = base * subgroup_importance * (1.0 + 0.25 * max(persistence - 1, 0))
    return float(min(10.0, raw * 2.0))


@dataclass
class AlertConfig:
    enabled: bool = True
    min_severity: str = "warn"  # "warn" or "alert"
    importance_by_dimension: dict[str, float] = field(
        default_factory=lambda: {
            "site_id": 1.5,
            "scanner_id": 1.2,
            "stain": 1.2,
            "tissue_type": 1.0,
            "cohort": 1.3,
        }
    )


@dataclass
class Alert:
    key: str
    title: str
    severity: str
    score: float
    category: str  # "drift" | "calibration" | "subgroup" | "plausibility" | "silent_failure" | "schema"
    root_cause_hint: str | None = None
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "severity": self.severity,
            "score": self.score,
            "category": self.category,
            "root_cause_hint": self.root_cause_hint,
            "details": self.details,
        }


def _key(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return h[:12]


class AlertEngine:
    def __init__(self, config: AlertConfig | None = None) -> None:
        self.config = config or AlertConfig()

    def _passes_min(self, severity: str) -> bool:
        return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(self.config.min_severity, 1)

    def from_drift(self, results: Iterable, *, kind: str = "input") -> list[Alert]:
        alerts: list[Alert] = []
        for r in results:
            if not self._passes_min(r.severity):
                continue
            key = _key("drift", kind, r.name)
            alerts.append(
                Alert(
                    key=key,
                    title=f"{kind} drift: {r.name} = {r.value:.4f}",
                    severity=r.severity,
                    score=severity_score(r.severity),
                    category="drift",
                    root_cause_hint=kind,
                    details=r.as_dict(),
                )
            )
        return alerts

    def from_calibration(self, results: Iterable) -> list[Alert]:
        alerts: list[Alert] = []
        for r in results:
            if not self._passes_min(r.severity):
                continue
            alerts.append(
                Alert(
                    key=_key("calibration", r.name),
                    title=f"calibration drift: {r.name} = {r.value:.4f}",
                    severity=r.severity,
                    score=severity_score(r.severity),
                    category="calibration",
                    root_cause_hint=None,
                    details=r.as_dict(),
                )
            )
        return alerts

    def from_subgroups(self, results: Iterable) -> list[Alert]:
        alerts: list[Alert] = []
        for r in results:
            if not self._passes_min(r.severity):
                continue
            importance = self.config.importance_by_dimension.get(r.dimension, 1.0)
            alerts.append(
                Alert(
                    key=_key("subgroup", r.dimension, r.value, r.metric),
                    title=(
                        f"subgroup degradation in {r.dimension}={r.value!r}: "
                        f"{r.metric}={r.metric_value:.3f} (Δ={r.delta:+.3f})"
                    ),
                    severity=r.severity,
                    score=severity_score(r.severity, subgroup_importance=importance),
                    category="subgroup",
                    root_cause_hint=f"{r.dimension}={r.value}",
                    details=r.as_dict(),
                )
            )
        return alerts

    def from_plausibility(self, violations: Iterable) -> list[Alert]:
        alerts: list[Alert] = []
        for v in violations:
            if not self._passes_min(v.severity):
                continue
            alerts.append(
                Alert(
                    key=_key("plausibility", v.rule, v.record_id or ""),
                    title=f"plausibility violation: {v.rule}",
                    severity=v.severity,
                    score=severity_score(v.severity, subgroup_importance=1.4),
                    category="plausibility",
                    root_cause_hint=v.rule,
                    details=v.as_dict(),
                )
            )
        return alerts

    def from_silent_failure(self, results: Iterable) -> list[Alert]:
        alerts: list[Alert] = []
        for r in results:
            if not self._passes_min(r.severity):
                continue
            alerts.append(
                Alert(
                    key=_key("silent_failure", r.name),
                    title=f"silent failure signature: {r.name} = {r.value:.4f}",
                    severity=r.severity,
                    score=severity_score(r.severity, subgroup_importance=1.5),
                    category="silent_failure",
                    root_cause_hint=r.name,
                    details=r.as_dict(),
                )
            )
        return alerts

    @staticmethod
    def deduplicate(alerts: Iterable[Alert]) -> list[Alert]:
        """Drop duplicate keys, keeping the highest-scoring instance."""
        best: dict[str, Alert] = {}
        for a in alerts:
            cur = best.get(a.key)
            if cur is None or a.score > cur.score:
                best[a.key] = a
        return sorted(best.values(), key=lambda a: -a.score)
