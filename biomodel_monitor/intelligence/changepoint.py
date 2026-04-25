"""Changepoint detection on metric time series.

Implements a CUSUM-style detector with a top-down divisive segmentation. The
method is robust to small noise and produces interpretable break-points
(``"this metric stepped up around point i"``) which is the form medical
operators ask for.

The algorithm:

1. Compute the cumulative sum of (x_i - mean(x)).
2. Find the index that maximises ``|S_i|``; that is a candidate changepoint.
3. Accept it if the segment-mean shift exceeds a threshold relative to the
   pooled standard deviation.
4. Recurse on the left and right halves until min-segment-size is reached.

This is intentionally pure-Python + NumPy. Scaling to many millions of points
isn't a goal — the typical input is ~50 to a few hundred run-level metric
values per (model, version, name) tuple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ChangepointResult:
    indices: list[int] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"indices": self.indices, "segments": self.segments}


def _segment_dict(start: int, end: int, values: np.ndarray) -> dict[str, Any]:
    chunk = values[start:end]
    return {
        "start": int(start),
        "end": int(end),
        "n": int(chunk.size),
        "mean": float(chunk.mean()) if chunk.size else 0.0,
        "std": float(chunk.std(ddof=0)) if chunk.size else 0.0,
    }


def _find_one(values: np.ndarray, *, min_size: int, threshold: float) -> int | None:
    n = values.size
    if n < 2 * min_size:
        return None
    centered = values - values.mean()
    cumsum = np.cumsum(centered)
    # ignore the boundary candidates
    valid = cumsum.copy()
    valid[: min_size - 1] = 0.0
    valid[-min_size:] = 0.0
    idx = int(np.argmax(np.abs(valid)))
    if idx < min_size or idx > n - min_size:
        return None
    left = values[:idx]
    right = values[idx:]
    pooled = float(np.std(values, ddof=0))
    if pooled == 0.0:
        return None
    shift = abs(left.mean() - right.mean()) / pooled
    if shift < threshold:
        return None
    return idx


def detect_changepoints(
    series: list[float] | np.ndarray,
    *,
    min_size: int = 5,
    threshold: float = 1.0,
    max_changepoints: int = 10,
) -> ChangepointResult:
    """Detect changepoints in ``series``.

    Parameters
    ----------
    series:
        1-D numeric sequence (one value per run for a given metric).
    min_size:
        Minimum points a segment must contain to be split further.
    threshold:
        Required |Δmean| / pooled-σ for a candidate to be accepted.
    max_changepoints:
        Hard cap on accepted changepoints (defends against pathological inputs).
    """
    arr = np.asarray(list(series), dtype=float)
    if arr.size < 2 * min_size:
        return ChangepointResult(
            indices=[], segments=[_segment_dict(0, arr.size, arr)] if arr.size else [],
        )

    accepted: list[int] = []
    pending: list[tuple[int, int]] = [(0, arr.size)]
    while pending and len(accepted) < max_changepoints:
        start, end = pending.pop(0)
        cp = _find_one(arr[start:end], min_size=min_size, threshold=threshold)
        if cp is None:
            continue
        absolute = start + cp
        accepted.append(absolute)
        pending.append((start, absolute))
        pending.append((absolute, end))

    accepted.sort()
    boundaries = [0, *accepted, arr.size]
    segments = [
        _segment_dict(boundaries[i], boundaries[i + 1], arr)
        for i in range(len(boundaries) - 1)
    ]
    return ChangepointResult(indices=accepted, segments=segments)


__all__ = ["ChangepointResult", "detect_changepoints"]
