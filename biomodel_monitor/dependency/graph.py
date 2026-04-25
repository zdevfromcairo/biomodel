"""Cross-model dependency graph and attribution."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from biomodel_monitor.alerts.engine import Alert


@dataclass(frozen=True)
class DependencyEdge:
    upstream: str          # upstream model_id
    downstream: str        # downstream model_id
    note: str = ""


@dataclass
class DependencyGraph:
    edges: list[DependencyEdge] = field(default_factory=list)

    def add(self, upstream: str, downstream: str, note: str = "") -> None:
        if upstream == downstream:
            raise ValueError("self-dependency not allowed")
        edge = DependencyEdge(upstream, downstream, note)
        if edge not in self.edges:
            self.edges.append(edge)

    def downstream_of(self, model_id: str) -> set[str]:
        adj: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            adj[e.upstream].append(e.downstream)
        out: set[str] = set()
        q = deque([model_id])
        while q:
            cur = q.popleft()
            for n in adj.get(cur, []):
                if n not in out:
                    out.add(n)
                    q.append(n)
        return out

    def upstream_of(self, model_id: str) -> set[str]:
        adj: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            adj[e.downstream].append(e.upstream)
        out: set[str] = set()
        q = deque([model_id])
        while q:
            cur = q.popleft()
            for n in adj.get(cur, []):
                if n not in out:
                    out.add(n)
                    q.append(n)
        return out

    def detect_cycles(self) -> bool:
        adj: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            adj[e.upstream].append(e.downstream)
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {}
        nodes: set[str] = set()
        for e in self.edges:
            nodes.add(e.upstream)
            nodes.add(e.downstream)

        def dfs(u: str) -> bool:
            color[u] = GRAY
            for v in adj.get(u, []):
                c = color.get(v, WHITE)
                if c == GRAY:
                    return True
                if c == WHITE and dfs(v):
                    return True
            color[u] = BLACK
            return False

        return any(color.get(n, WHITE) == WHITE and dfs(n) for n in nodes)


@dataclass
class Impact:
    upstream_model_id: str
    downstream_model_id: str
    explained_alert_keys: list[str]
    n_explained: int

    def as_dict(self) -> dict:
        return {
            "upstream_model_id": self.upstream_model_id,
            "downstream_model_id": self.downstream_model_id,
            "explained_alert_keys": self.explained_alert_keys,
            "n_explained": self.n_explained,
        }


def attribute_alerts(
    graph: DependencyGraph,
    upstream_model_id: str,
    downstream_alerts: Iterable[tuple[str, Alert]],
) -> list[Impact]:
    """Given an upstream regression, attribute downstream alerts to it.

    ``downstream_alerts`` is an iterable of ``(downstream_model_id, alert)``.
    Only alerts whose model is reachable from ``upstream_model_id`` are
    attributed; results are grouped per downstream model.
    """
    reachable = graph.downstream_of(upstream_model_id)
    grouped: dict[str, list[str]] = defaultdict(list)
    for dm, a in downstream_alerts:
        if dm in reachable:
            grouped[dm].append(a.key)
    return [
        Impact(
            upstream_model_id=upstream_model_id,
            downstream_model_id=dm,
            explained_alert_keys=keys,
            n_explained=len(keys),
        )
        for dm, keys in sorted(grouped.items())
    ]
