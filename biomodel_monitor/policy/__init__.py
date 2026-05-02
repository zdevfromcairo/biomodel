"""Declarative governance policies (v0.9).

Operators write rules like:

    policies:
      - name: quarantine-on-persistent-ece
        when:
          metric: ece
          severity_at_least: alert
          persistence_at_least: 3
          cohort: ICU                 # optional dimension match
        action: quarantine
        message: "ECE alert in ICU has persisted for 3+ runs"

      - name: notify-on-mmd-warn
        when:
          metric: mmd_rbf
          severity_at_least: warn
        action: notify
        message: "Embedding drift detected"

The :class:`PolicyEngine` evaluates each policy against an alert + run context
and returns a list of :class:`PolicyAction` instances. The server then
executes those actions (today: ``notify``, ``quarantine``, ``promote``).

Policies are intentionally declarative — the schema is YAML/JSON and there
is no embedded Python — so they can ship as configuration, be reviewed, and
sit in source control next to the model code they govern.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY_RANK = {"ok": 0, "warn": 1, "alert": 2}
ALLOWED_ACTIONS = {"notify", "quarantine", "promote"}


@dataclass
class PolicyMatch:
    """Conditions a policy applies to."""

    metric: str | None = None        # exact match against alert.details["name"]/category
    severity_at_least: str | None = None
    persistence_at_least: int | None = None
    cohort: str | None = None
    site_id: str | None = None
    scanner_id: str | None = None
    category: str | None = None      # alert.category exact match


@dataclass
class Policy:
    name: str
    when: PolicyMatch
    action: str
    message: str = ""

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError(
                f"unknown policy action {self.action!r}; "
                f"must be one of {sorted(ALLOWED_ACTIONS)}",
            )
        if self.when.severity_at_least and self.when.severity_at_least not in SEVERITY_RANK:
            raise ValueError(
                f"unknown severity {self.when.severity_at_least!r}",
            )

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> Policy:
        when_body = dict(body.get("when") or {})
        return cls(
            name=str(body.get("name", "policy")),
            when=PolicyMatch(**when_body),
            action=str(body.get("action", "notify")),
            message=str(body.get("message", "")),
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class PolicyAction:
    policy: str
    action: str
    message: str
    alert_key: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class PolicyEngine:
    """Evaluate a set of policies against an alert + run context."""

    def __init__(self, policies: Iterable[Policy] | None = None) -> None:
        self.policies: list[Policy] = list(policies or [])

    def add(self, policy: Policy) -> None:
        self.policies.append(policy)

    @classmethod
    def from_yaml(cls, text: str) -> PolicyEngine:
        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PyYAML required to parse policy YAML") from e
        body = yaml.safe_load(text) or {}
        rules = body.get("policies", [])
        return cls([Policy.from_dict(r) for r in rules])

    @staticmethod
    def _alert_severity(alert: Mapping[str, Any]) -> int:
        return SEVERITY_RANK.get(str(alert.get("severity", "ok")), 0)

    @staticmethod
    def _alert_cohort(alert: Mapping[str, Any]) -> str | None:
        details = alert.get("details") or {}
        return details.get("cohort") or details.get("value") or alert.get("cohort")

    @staticmethod
    def _alert_metric(alert: Mapping[str, Any]) -> str:
        details = alert.get("details") or {}
        return str(details.get("name") or details.get("kind") or alert.get("category", ""))

    def _matches(self, policy: Policy, alert: Mapping[str, Any]) -> bool:
        w = policy.when
        if w.severity_at_least is not None:
            if self._alert_severity(alert) < SEVERITY_RANK[w.severity_at_least]:
                return False
        if w.persistence_at_least is not None:
            if int(alert.get("persistence", 1)) < int(w.persistence_at_least):
                return False
        if w.metric is not None:
            am = self._alert_metric(alert)
            if w.metric not in am and w.metric != am:
                return False
        if w.category is not None and str(alert.get("category", "")) != w.category:
            return False
        details = alert.get("details") or {}
        if w.cohort is not None and str(self._alert_cohort(alert) or "") != w.cohort:
            return False
        if w.site_id is not None and str(details.get("site_id") or "") != w.site_id:
            return False
        if w.scanner_id is not None and str(details.get("scanner_id") or "") != w.scanner_id:
            return False
        return True

    def evaluate(
        self,
        alerts: Iterable[Mapping[str, Any]],
        *,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> list[PolicyAction]:
        out: list[PolicyAction] = []
        for alert in alerts:
            for policy in self.policies:
                if self._matches(policy, alert):
                    out.append(PolicyAction(
                        policy=policy.name,
                        action=policy.action,
                        message=policy.message or f"policy {policy.name} matched",
                        alert_key=str(alert.get("key", "")),
                        model_id=model_id,
                        model_version=model_version,
                        extra={"severity": alert.get("severity"),
                               "persistence": alert.get("persistence", 1)},
                    ))
        return out


__all__ = ["Policy", "PolicyAction", "PolicyEngine", "PolicyMatch"]
