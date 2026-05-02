"""Drift influence graph (v0.8).

Single-dimension attribution tells you *which* dimension is to blame.
A drift graph tells you *how dimensions move together* — when scanner X
appears, cohort Y appears too, and the apparent "scanner drift" is really
"cohort drift seen through a scanner lens".

We build a directed graph where:

* nodes are ``(dimension, value)`` pairs, plus the special root ``__pooled__``,
* edges ``(d1=v1) → (d2=v2)`` carry an influence weight =
  the *conditional drift* of d2's distribution given that d1=v1, measured
  vs. the pooled current-period distribution of d2.

The result is a compact graph the operator can read end-to-end:
"`scanner=Aperio` shifts the `cohort` distribution by JS=0.41 — that's why
the cohort=ICU PSI looks elevated".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from biomodel_monitor.schema.models import PredictionRecord

DEFAULT_DIMENSIONS = ("site_id", "scanner_id", "stain", "tissue_type", "cohort")


@dataclass
class GraphNode:
    id: str
    dimension: str | None
    value: Any | None
    n: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphEdge:
    source: str
    target_dimension: str
    weight: float  # JS divergence in [0, 1]
    p_conditional: dict[str, float]
    p_pooled: dict[str, float]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DriftGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    severity: str = "ok"
    notes: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": [e.as_dict() for e in self.edges],
            "severity": self.severity,
            "notes": self.notes,
            "extra": self.extra,
        }

    def to_dot(self) -> str:
        """Render the graph as Graphviz ``dot`` source (for docs/exports)."""
        lines = ["digraph drift {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
        for node in self.nodes:
            label = node.id.replace('"', '\\"')
            lines.append(f'  "{node.id}" [label="{label}\\nn={node.n}"];')
        for e in self.edges:
            lines.append(
                f'  "{e.source}" -> "dim:{e.target_dimension}" '
                f'[label="JS={e.weight:.3f}"];'
            )
        lines.append("}")
        return "\n".join(lines)


def _pull(rec: PredictionRecord, dim: str) -> Any:
    v = getattr(rec, dim, None)
    if v is None and rec.features is not None:
        v = rec.features.get(dim)
    return v


def _normalise(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    if total == 0:
        return {}
    return {str(k): v / total for k, v in counter.items()}


def _js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen–Shannon divergence in *bits* between two discrete distributions."""
    keys = set(p) | set(q)
    if not keys:
        return 0.0

    def _kl(a: dict[str, float], b: dict[str, float]) -> float:
        s = 0.0
        for k in keys:
            ak = a.get(k, 0.0)
            bk = b.get(k, 0.0)
            if ak > 0 and bk > 0:
                s += ak * math.log2(ak / bk)
        return s

    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}
    return 0.5 * _kl(p, m) + 0.5 * _kl(q, m)


def build_drift_graph(
    records: Iterable[PredictionRecord],
    *,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    min_n: int = 20,
    warn_js: float = 0.10,
    alert_js: float = 0.25,
    top_k_per_dim: int = 3,
) -> DriftGraph:
    """Build the drift influence graph for a batch of records.

    Parameters
    ----------
    records:
        Records that make up the period of interest (typically one batch).
    dimensions:
        Categorical dimensions to consider as both sources and targets.
    min_n:
        Skip a source node if fewer than this many records satisfy it; the
        conditional distribution would be too noisy to compare.
    top_k_per_dim:
        Keep at most this many edges per *target dimension* — the strongest
        influencers — so the graph stays readable.
    warn_js, alert_js:
        Severity is the worst edge weight. Defaults align with the existing
        JS-divergence severity scale used elsewhere in the project.
    """
    recs = list(records)
    if not recs:
        return DriftGraph(nodes=[], edges=[], severity="ok", notes="empty input")

    pooled: dict[str, dict[str, float]] = {}
    counts: dict[str, Counter] = {}
    for d in dimensions:
        c = Counter(str(_pull(r, d)) for r in recs if _pull(r, d) is not None)
        counts[d] = c
        pooled[d] = _normalise(c)

    nodes: dict[str, GraphNode] = {
        "__pooled__": GraphNode(id="__pooled__", dimension=None, value=None, n=len(recs)),
    }
    for d in dimensions:
        nodes[f"dim:{d}"] = GraphNode(
            id=f"dim:{d}", dimension=d, value=None, n=int(sum(counts[d].values())),
        )
        for v, n in counts[d].items():
            nid = f"{d}={v}"
            nodes[nid] = GraphNode(id=nid, dimension=d, value=v, n=int(n))

    edges_per_target: dict[str, list[GraphEdge]] = {d: [] for d in dimensions}
    for src_dim in dimensions:
        for src_val, n_src in counts[src_dim].items():
            if n_src < min_n:
                continue
            sub = [r for r in recs if str(_pull(r, src_dim)) == src_val]
            for tgt_dim in dimensions:
                if tgt_dim == src_dim:
                    continue
                sub_counter = Counter(
                    str(_pull(r, tgt_dim)) for r in sub if _pull(r, tgt_dim) is not None
                )
                if sum(sub_counter.values()) == 0:
                    continue
                p_cond = _normalise(sub_counter)
                p_pool = pooled[tgt_dim]
                if not p_pool:
                    continue
                w = _js_divergence(p_cond, p_pool)
                edges_per_target[tgt_dim].append(GraphEdge(
                    source=f"{src_dim}={src_val}",
                    target_dimension=tgt_dim,
                    weight=float(w),
                    p_conditional=p_cond,
                    p_pooled=p_pool,
                ))

    edges: list[GraphEdge] = []
    for _dim, edge_list in edges_per_target.items():
        edge_list.sort(key=lambda e: e.weight, reverse=True)
        edges.extend(edge_list[:top_k_per_dim])

    if edges:
        worst = max(e.weight for e in edges)
    else:
        worst = 0.0
    if worst >= alert_js:
        sev = "alert"
    elif worst >= warn_js:
        sev = "warn"
    else:
        sev = "ok"

    return DriftGraph(
        nodes=list(nodes.values()),
        edges=edges,
        severity=sev,
        notes=None if edges else "no source nodes met min_n",
        extra={
            "min_n": min_n,
            "warn_js": warn_js,
            "alert_js": alert_js,
            "top_k_per_dim": top_k_per_dim,
            "worst_weight": worst,
        },
    )


__all__ = ["DriftGraph", "GraphEdge", "GraphNode", "build_drift_graph"]
