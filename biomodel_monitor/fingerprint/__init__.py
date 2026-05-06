"""Model fingerprinting via canary-input prediction hashing (v0.11).

Quarantine + SBOM + audit log answer "*who*" and "*what*". This module
answers the third question every regulator asks: "*how do you know the
model running in production is still the model you signed off on?*"

The recipe is straightforward:

1. Fix a small, immutable set of *canary inputs*. Identify the set by an
   opaque ``canary_inputs_id`` (e.g. its content hash).
2. Run the model under test on those inputs and capture the prediction
   vectors.
3. Quantise the predictions to 6 decimal places (so harmless floating-point
   noise from CUDA non-determinism doesn't fail the check) and feed them
   into SHA-256.

Two identical models always produce the same fingerprint; any silent
substitution of weights, model architecture, or pre-processing will flip it.

The quantisation precision and a small ``schema_version`` are baked into
the digest so we can evolve the format without ambiguity.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

SCHEMA_VERSION = 1
QUANTISATION_DECIMALS = 6


@dataclass
class Fingerprint:
    canary_inputs_id: str
    fingerprint: str
    n_predictions: int
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return {
            "canary_inputs_id": self.canary_inputs_id,
            "fingerprint": self.fingerprint,
            "n_predictions": self.n_predictions,
            "schema_version": self.schema_version,
        }


def _quantise(predictions: Sequence[Sequence[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    q = QUANTISATION_DECIMALS
    for row in predictions:
        if not row:
            raise ValueError("each prediction row must be non-empty")
        new_row: list[float] = []
        for v in row:
            x = float(v)
            if not math.isfinite(x):
                raise ValueError("predictions must be finite")
            new_row.append(round(x, q))
        out.append(new_row)
    return out


def compute_fingerprint(canary_inputs_id: str,
                        predictions: Sequence[Sequence[float]]) -> Fingerprint:
    """Hash a model's predictions on a fixed canary input set.

    The same model on the same inputs always returns the same fingerprint;
    any silent change in weights or pre-processing will produce a new digest.
    """
    if not canary_inputs_id:
        raise ValueError("canary_inputs_id must be a non-empty string")
    if not predictions:
        raise ValueError("predictions must be non-empty")
    qpreds = _quantise(predictions)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "quantisation_decimals": QUANTISATION_DECIMALS,
        "canary_inputs_id": canary_inputs_id,
        "predictions": qpreds,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(blob).hexdigest()
    return Fingerprint(
        canary_inputs_id=canary_inputs_id,
        fingerprint=f"sha256:{digest}",
        n_predictions=len(qpreds),
    )


def compare_fingerprints(expected: str, actual: str) -> dict:
    """Constant-time fingerprint comparison with a v0.1-style severity hint."""
    matches = bool(expected) and bool(actual) and _const_eq(expected, actual)
    return {
        "matches": matches,
        "expected": expected,
        "actual": actual,
        "severity": "ok" if matches else "alert",
    }


def _const_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b, strict=True):
        diff |= ord(x) ^ ord(y)
    return diff == 0


__all__ = [
    "Fingerprint",
    "QUANTISATION_DECIMALS",
    "SCHEMA_VERSION",
    "compare_fingerprints",
    "compute_fingerprint",
]
