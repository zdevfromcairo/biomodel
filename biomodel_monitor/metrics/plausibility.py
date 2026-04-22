"""Domain-specific plausibility checks.

A rule takes the records of a batch and returns zero or more
:class:`PlausibilityViolation`. Rules are registered in a :class:`RuleRegistry`
so vendors can add their own without modifying the core package.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field


@dataclass
class PlausibilityViolation:
    rule: str
    record_id: str | None
    severity: str  # "warn" | "alert"
    message: str
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "record_id": self.record_id,
            "severity": self.severity,
            "message": self.message,
            "extra": self.extra,
        }


PlausibilityRule = Callable[[Sequence], list[PlausibilityViolation]]


class RuleRegistry:
    """Registry of plausibility rules. Plug-in friendly."""

    def __init__(self) -> None:
        self._rules: dict[str, PlausibilityRule] = {}

    def register(self, name: str, rule: PlausibilityRule) -> None:
        if name in self._rules:
            raise ValueError(f"rule {name!r} already registered")
        self._rules[name] = rule

    def names(self) -> list[str]:
        return sorted(self._rules)

    def run(self, records: Sequence) -> list[PlausibilityViolation]:
        out: list[PlausibilityViolation] = []
        for name, rule in self._rules.items():
            try:
                out.extend(rule(records))
            except Exception as e:  # rules must never crash the pipeline
                out.append(
                    PlausibilityViolation(
                        rule=name,
                        record_id=None,
                        severity="warn",
                        message=f"rule {name!r} raised: {e!r}",
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Pathology starter rules.
#
# These are intentionally simple. They assume optional fields under
# ``record.extra`` or ``record.features`` that pathology pipelines commonly
# emit. Each rule is documented so vendors can wire their own data into it.
# ---------------------------------------------------------------------------


def _f(record, *keys, default=None):
    """Look up ``keys`` first in record.extra then record.features."""
    for src in (getattr(record, "extra", None), getattr(record, "features", None)):
        if isinstance(src, dict):
            for k in keys:
                if k in src and src[k] is not None:
                    return src[k]
    return default


def rule_tumor_probability_vs_tissue_area(
    records: Iterable,
) -> list[PlausibilityViolation]:
    """Tumor probability cannot exceed the tissue-area fraction of the slide."""
    out: list[PlausibilityViolation] = []
    for r in records:
        tissue = _f(r, "tissue_area_fraction")
        if tissue is None:
            continue
        if r.score > float(tissue) + 1e-3:
            out.append(
                PlausibilityViolation(
                    rule="tumor_probability_vs_tissue_area",
                    record_id=r.prediction_id,
                    severity="alert",
                    message=(
                        f"tumor score {r.score:.3f} exceeds tissue area fraction "
                        f"{float(tissue):.3f}"
                    ),
                    extra={"tissue_area_fraction": float(tissue)},
                )
            )
    return out


def rule_mitosis_count_vs_field_area(
    records: Iterable,
) -> list[PlausibilityViolation]:
    """Mitotic count must be consistent with magnification and field area.

    Implausible if mitoses-per-mm² exceeds a hard cap (default 200/mm²) which
    is well above any biologically observed density.
    """
    cap = 200.0
    out: list[PlausibilityViolation] = []
    for r in records:
        mitoses = _f(r, "mitosis_count")
        area_mm2 = _f(r, "field_area_mm2")
        if mitoses is None or area_mm2 is None or float(area_mm2) <= 0:
            continue
        density = float(mitoses) / float(area_mm2)
        if density > cap:
            out.append(
                PlausibilityViolation(
                    rule="mitosis_count_vs_field_area",
                    record_id=r.prediction_id,
                    severity="alert",
                    message=(
                        f"mitotic density {density:.1f}/mm² exceeds biologically "
                        f"plausible cap of {cap:.0f}/mm²"
                    ),
                    extra={"mitoses": float(mitoses), "field_area_mm2": float(area_mm2)},
                )
            )
    return out


def rule_mutually_exclusive_markers(
    records: Iterable,
) -> list[PlausibilityViolation]:
    """Markers declared mutually exclusive must not both be strongly positive.

    Reads ``extra['markers']`` (dict marker->probability) and
    ``extra['exclusive_marker_pairs']`` (list of [m1, m2] pairs). Both > 0.5
    triggers a violation.
    """
    out: list[PlausibilityViolation] = []
    for r in records:
        markers = _f(r, "markers")
        pairs = _f(r, "exclusive_marker_pairs")
        if not isinstance(markers, dict) or not isinstance(pairs, list):
            continue
        for pair in pairs:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                continue
            a, b = pair
            va = markers.get(a)
            vb = markers.get(b)
            if va is None or vb is None:
                continue
            if float(va) > 0.5 and float(vb) > 0.5:
                out.append(
                    PlausibilityViolation(
                        rule="mutually_exclusive_markers",
                        record_id=r.prediction_id,
                        severity="alert",
                        message=(
                            f"markers {a!r} and {b!r} are mutually exclusive but "
                            f"both positive ({float(va):.2f}, {float(vb):.2f})"
                        ),
                        extra={"pair": [a, b], "values": [float(va), float(vb)]},
                    )
                )
    return out


def rule_slide_tile_consistency(
    records: Iterable,
) -> list[PlausibilityViolation]:
    """Slide-level prediction must be consistent with aggregated tile predictions.

    Reads ``extra['tile_scores']`` (list[float]). If the mean tile score and
    slide score disagree by more than 0.30, flag it.
    """
    tol = 0.30
    out: list[PlausibilityViolation] = []
    for r in records:
        tiles = _f(r, "tile_scores")
        if not isinstance(tiles, (list, tuple)) or len(tiles) == 0:
            continue
        mean_tile = sum(float(x) for x in tiles) / len(tiles)
        diff = abs(mean_tile - r.score)
        if diff > tol:
            out.append(
                PlausibilityViolation(
                    rule="slide_tile_consistency",
                    record_id=r.prediction_id,
                    severity="warn",
                    message=(
                        f"slide score {r.score:.3f} disagrees with mean tile "
                        f"score {mean_tile:.3f} by {diff:.3f} (tol={tol})"
                    ),
                    extra={"mean_tile_score": mean_tile, "n_tiles": len(tiles)},
                )
            )
    return out


def builtin_pathology_rules() -> RuleRegistry:
    """Return a registry preloaded with the pathology starter rules."""
    reg = RuleRegistry()
    reg.register("tumor_probability_vs_tissue_area", rule_tumor_probability_vs_tissue_area)
    reg.register("mitosis_count_vs_field_area", rule_mitosis_count_vs_field_area)
    reg.register("mutually_exclusive_markers", rule_mutually_exclusive_markers)
    reg.register("slide_tile_consistency", rule_slide_tile_consistency)
    return reg
