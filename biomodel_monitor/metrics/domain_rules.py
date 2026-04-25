"""Radiology and multimodal-omics plausibility rule packs.

These plug into the same :class:`RuleRegistry` as the pathology rules. Each
returns ``PlausibilityViolation`` with the same severity convention.
"""

from __future__ import annotations

from collections.abc import Iterable

from biomodel_monitor.metrics.plausibility import (
    PlausibilityViolation,
    RuleRegistry,
)
from biomodel_monitor.schema.models import PredictionRecord


# ---------------------------------------------------------------------------
# Radiology
# ---------------------------------------------------------------------------
def laterality_consistency(records: Iterable[PredictionRecord]) -> list[PlausibilityViolation]:
    """A finding marked ``laterality=left`` must not depend on right-side features.

    Convention used here: ``record.extra['laterality']`` ∈ {"left","right"} and
    feature names prefixed ``rt_`` denote right-side scores while ``lt_`` denote
    left-side. If a record marked one side carries a strong opposite-side
    signal that drives the prediction, fire.
    """
    out: list[PlausibilityViolation] = []
    for r in records:
        side_val = ""
        if r.extra and isinstance(r.extra.get("laterality"), str):
            side_val = str(r.extra["laterality"]).lower()
        elif r.tissue_type:
            side_val = r.tissue_type.lower()
        if side_val not in {"left", "right"}:
            continue
        feats = r.features or {}
        wrong_prefix = "rt_" if side_val == "left" else "lt_"
        opposite = [k for k in feats if k.startswith(wrong_prefix)]
        if not opposite:
            continue
        opp_max = max(float(feats[k]) for k in opposite)
        if r.score >= 0.5 and opp_max >= 0.7:
            out.append(
                PlausibilityViolation(
                    rule="laterality_consistency",
                    severity="alert",
                    record_id=r.prediction_id,
                    message=(
                        f"prediction marked laterality={side_val} but driven by "
                        f"opposite-side features (max={opp_max:.2f})"
                    ),
                )
            )
    return out


def anatomy_prior(records: Iterable[PredictionRecord]) -> list[PlausibilityViolation]:
    """Predicted region cannot lie outside the imaged anatomy.

    Uses ``features['region_in_fov']`` as a 0..1 score for "the predicted
    region is inside the field of view". A high prediction with a low FOV
    score is a strong silent failure indicator.
    """
    out: list[PlausibilityViolation] = []
    for r in records:
        if not r.features:
            continue
        fov = r.features.get("region_in_fov")
        if fov is None:
            continue
        if r.score >= 0.7 and float(fov) <= 0.2:
            out.append(
                PlausibilityViolation(
                    rule="anatomy_prior",
                    severity="alert",
                    record_id=r.prediction_id,
                    message=(
                        f"high prediction ({r.score:.2f}) but region_in_fov={float(fov):.2f}"
                    ),
                )
            )
    return out


def modality_cross_check(records: Iterable[PredictionRecord]) -> list[PlausibilityViolation]:
    """If two modalities cover the same finding they should broadly agree.

    Convention: ``features['modality_secondary_score']`` carries the score
    from a co-registered modality (e.g. CT vs. MRI). A primary positive with a
    secondary near-zero is suspicious.
    """
    out: list[PlausibilityViolation] = []
    for r in records:
        if not r.features:
            continue
        sec = r.features.get("modality_secondary_score")
        if sec is None:
            continue
        gap = abs(r.score - float(sec))
        if r.score >= 0.7 and float(sec) <= 0.1:
            out.append(
                PlausibilityViolation(
                    rule="modality_cross_check",
                    severity="warn",
                    record_id=r.prediction_id,
                    message=(
                        f"primary score {r.score:.2f} disagrees with secondary modality "
                        f"{float(sec):.2f} (gap={gap:.2f})"
                    ),
                )
            )
    return out


def builtin_radiology_rules() -> RuleRegistry:
    reg = RuleRegistry()
    reg.register("laterality_consistency", laterality_consistency)
    reg.register("anatomy_prior", anatomy_prior)
    reg.register("modality_cross_check", modality_cross_check)
    return reg


# ---------------------------------------------------------------------------
# Multimodal omics
# ---------------------------------------------------------------------------
def expression_bounds(records: Iterable[PredictionRecord]) -> list[PlausibilityViolation]:
    """Expression-level features must lie in their declared ranges (TPM ≥ 0)."""
    out: list[PlausibilityViolation] = []
    for r in records:
        if not r.features:
            continue
        for name, val in r.features.items():
            if name.endswith("_tpm") or name.endswith("_expr"):
                v = float(val)
                if v < 0 or v > 1e6:
                    out.append(
                        PlausibilityViolation(
                            rule="expression_bounds",
                            severity="alert",
                            record_id=r.prediction_id,
                            message=f"{name}={v:.2f} outside plausible expression range",
                        )
                    )
    return out


def pathway_consistency(records: Iterable[PredictionRecord]) -> list[PlausibilityViolation]:
    """Members of an upregulated pathway should not contradict each other.

    Pairs are read from ``record.extra['pathway_pairs']`` as a list of
    ``[a, b, sign]`` triples; ``sign=+1`` means the two members move together,
    ``sign=-1`` means inversely. A strong contradiction fires.
    """
    out: list[PlausibilityViolation] = []
    for r in records:
        if not r.extra:
            continue
        pairs = r.extra.get("pathway_pairs")
        if not isinstance(pairs, list):
            continue
        for entry in pairs:
            if not (isinstance(entry, (list, tuple)) and len(entry) == 3):
                continue
            a_v, b_v, sign = entry
            try:
                a = float(a_v)
                b = float(b_v)
                s = int(sign)
            except (TypeError, ValueError):
                continue
            if s not in (-1, 1):
                continue
            # both strongly expressed but in the wrong direction
            if abs(a) >= 1.0 and abs(b) >= 1.0:
                same_sign = (a > 0) == (b > 0)
                if (s == 1 and not same_sign) or (s == -1 and same_sign):
                    out.append(
                        PlausibilityViolation(
                            rule="pathway_consistency",
                            severity="warn",
                            record_id=r.prediction_id,
                            message=(
                                f"pathway pair contradicts declared sign={s}: "
                                f"a={a:.2f}, b={b:.2f}"
                            ),
                        )
                    )
    return out


def builtin_omics_rules() -> RuleRegistry:
    reg = RuleRegistry()
    reg.register("expression_bounds", expression_bounds)
    reg.register("pathway_consistency", pathway_consistency)
    return reg
