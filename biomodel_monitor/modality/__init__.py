"""Per-modality drift checks (v0.11).

The v0.1–v0.9 drift detectors are *modality-agnostic*: they work on any
numeric column. Real medical AI deployments are multi-modal, and the
relevant drift signals differ per modality:

* **Image** — resolution distribution, pixel intensity stats, mean
  brightness, channel count.
* **Text** — token-length distribution, type/token ratio, vocab overlap.
* **Tabular** — row-level missingness rate (per column).

Each validator takes lightweight aggregate statistics (already computed
upstream) and compares them between a *reference* and *current* batch.
We deliberately do not depend on Pillow / NLTK / transformers — the inputs
are plain dicts of summary numbers, easy to ship from any client.

All results follow the v0.1 severity contract.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModalityResult:
    name: str
    value: float           # magnitude of the largest per-feature drift
    severity: str = "ok"
    kind: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "severity": self.severity,
            "kind": self.kind,
            "extra": self.extra,
        }


def _bands(value: float, *, warn: float, alert: float) -> str:
    if value >= alert:
        return "alert"
    if value >= warn:
        return "warn"
    return "ok"


def _rel_diff(a: float, b: float) -> float:
    """Symmetric relative difference, well-behaved at 0."""
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


# --------------------------------------------------------------------------- #
# Image
# --------------------------------------------------------------------------- #


_IMAGE_KEYS = {"width_mean", "height_mean", "intensity_mean",
               "intensity_std", "channels"}


def check_image(reference: Mapping[str, Any], current: Mapping[str, Any],
                *, warn: float = 0.10, alert: float = 0.25) -> ModalityResult:
    """Compare image-batch summary stats.

    Both ``reference`` and ``current`` are mappings with at least the
    following keys (extras are ignored)::

        width_mean, height_mean, intensity_mean, intensity_std, channels
    """
    missing = _IMAGE_KEYS - reference.keys()
    if missing:
        raise ValueError(
            f"reference is missing required image keys: {sorted(missing)}"
        )
    missing = _IMAGE_KEYS - current.keys()
    if missing:
        raise ValueError(
            f"current is missing required image keys: {sorted(missing)}"
        )

    diffs: dict[str, float] = {}
    for key in ("width_mean", "height_mean", "intensity_mean", "intensity_std"):
        diffs[key] = _rel_diff(float(reference[key]), float(current[key]))
    channel_changed = int(reference["channels"]) != int(current["channels"])
    if channel_changed:
        # Channel-count change is categorical and severe.
        diffs["channels"] = 1.0

    worst_key = max(diffs, key=diffs.get)
    worst = diffs[worst_key]
    severity = _bands(worst, warn=warn, alert=alert)
    return ModalityResult(
        name="image_modality_drift",
        value=worst,
        severity=severity,
        kind="image",
        extra={
            "diffs": diffs,
            "worst_feature": worst_key,
            "channel_changed": channel_changed,
        },
    )


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #


_TEXT_KEYS = {"token_length_mean", "token_length_std", "type_token_ratio",
              "vocab_size"}


def _vocab_overlap(ref: Mapping[str, Any], cur: Mapping[str, Any]) -> float:
    """Optional Jaccard overlap on the (small) vocab if both sides ship one."""
    rv = set(ref.get("top_tokens", []) or [])
    cv = set(cur.get("top_tokens", []) or [])
    union = rv | cv
    if not union:
        return 1.0
    return len(rv & cv) / len(union)


def check_text(reference: Mapping[str, Any], current: Mapping[str, Any],
               *, warn: float = 0.10, alert: float = 0.25) -> ModalityResult:
    """Compare text-batch summary stats.

    Required keys: ``token_length_mean``, ``token_length_std``,
    ``type_token_ratio``, ``vocab_size``. Optional ``top_tokens`` (list of
    strings) drives a Jaccard vocab-overlap signal.
    """
    missing = _TEXT_KEYS - reference.keys()
    if missing:
        raise ValueError(
            f"reference is missing required text keys: {sorted(missing)}"
        )
    missing = _TEXT_KEYS - current.keys()
    if missing:
        raise ValueError(
            f"current is missing required text keys: {sorted(missing)}"
        )

    diffs: dict[str, float] = {}
    for key in _TEXT_KEYS:
        diffs[key] = _rel_diff(float(reference[key]), float(current[key]))
    overlap = _vocab_overlap(reference, current)
    diffs["vocab_overlap_loss"] = 1.0 - overlap

    worst_key = max(diffs, key=diffs.get)
    worst = diffs[worst_key]
    severity = _bands(worst, warn=warn, alert=alert)
    return ModalityResult(
        name="text_modality_drift",
        value=worst,
        severity=severity,
        kind="text",
        extra={
            "diffs": diffs,
            "vocab_overlap": overlap,
            "worst_feature": worst_key,
        },
    )


# --------------------------------------------------------------------------- #
# Tabular
# --------------------------------------------------------------------------- #


def check_tabular(reference: Mapping[str, Any], current: Mapping[str, Any],
                  *, warn: float = 0.10,
                  alert: float = 0.25) -> ModalityResult:
    """Compare per-column missingness fractions.

    ``reference`` and ``current`` should each be ``{"missingness": {col: p}}``
    where ``p`` is the fraction of rows missing that column.
    """
    ref_miss = reference.get("missingness")
    cur_miss = current.get("missingness")
    if not isinstance(ref_miss, Mapping) or not isinstance(cur_miss, Mapping):
        raise ValueError("both sides must include a 'missingness' dict")

    cols = sorted(set(ref_miss.keys()) | set(cur_miss.keys()))
    if not cols:
        raise ValueError("missingness dicts are empty")
    diffs: dict[str, float] = {}
    for c in cols:
        a = float(ref_miss.get(c, 0.0))
        b = float(cur_miss.get(c, 0.0))
        # Absolute difference in [0, 1] is more interpretable than relative
        # for missingness rates.
        diffs[c] = abs(a - b)

    worst_key = max(diffs, key=diffs.get)
    worst = diffs[worst_key]
    severity = _bands(worst, warn=warn, alert=alert)
    return ModalityResult(
        name="tabular_modality_drift",
        value=worst,
        severity=severity,
        kind="tabular",
        extra={
            "missingness_diffs": diffs,
            "worst_column": worst_key,
            "n_columns": len(cols),
        },
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


_DISPATCH = {
    "image": check_image,
    "text": check_text,
    "tabular": check_tabular,
}


def check_modality(kind: str,
                   reference: Mapping[str, Any],
                   current: Mapping[str, Any],
                   *, warn: float = 0.10,
                   alert: float = 0.25) -> ModalityResult:
    """Dispatch to the appropriate modality validator."""
    fn = _DISPATCH.get(kind)
    if fn is None:
        raise ValueError(
            f"unknown modality kind: {kind!r} (expected one of "
            f"{sorted(_DISPATCH)})"
        )
    return fn(reference, current, warn=warn, alert=alert)


__all__ = [
    "ModalityResult",
    "check_image",
    "check_modality",
    "check_tabular",
    "check_text",
]


# silence "unused" warnings for math import — leave room for future use.
_ = math
