"""Privacy-preserving aggregation across sites."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

# ----------------------------------------------------------- summary classes


@dataclass
class HistogramSummary:
    """Per-site histogram. ``counts`` length must match ``bin_edges`` - 1."""

    site_id: str
    bin_edges: list[float]   # length k + 1
    counts: list[int]        # length k

    def __post_init__(self) -> None:
        if len(self.counts) != len(self.bin_edges) - 1:
            raise ValueError("counts must have len(bin_edges) - 1")
        if any(c < 0 for c in self.counts):
            raise ValueError("counts must be non-negative")

    @property
    def n(self) -> int:
        return sum(self.counts)


@dataclass
class CalibrationSummary:
    """Per-site reliability bins for ECE.

    For each probability bin ``i``:
    * ``bin_count[i]``    — number of predictions falling in the bin,
    * ``bin_pos[i]``      — number of positives among them (ground truth),
    * ``bin_conf_sum[i]`` — sum of model probabilities (for mean confidence).
    """

    site_id: str
    bin_edges: list[float]   # length k + 1, in [0, 1]
    bin_count: list[int]
    bin_pos: list[int]
    bin_conf_sum: list[float]

    def __post_init__(self) -> None:
        k = len(self.bin_edges) - 1
        if not (len(self.bin_count) == len(self.bin_pos) == len(self.bin_conf_sum) == k):
            raise ValueError("bin_count, bin_pos, bin_conf_sum must all have len(bin_edges)-1")
        if any(c < 0 for c in self.bin_count) or any(p < 0 for p in self.bin_pos):
            raise ValueError("counts must be non-negative")

    @property
    def n(self) -> int:
        return sum(self.bin_count)


@dataclass
class MomentSummary:
    """Sufficient stats for a continuous metric (e.g. mean score)."""

    site_id: str
    n: int
    sum: float
    sum_sq: float

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError("n must be >= 0")


# --------------------------------------------------------------- result types


@dataclass
class FederatedDrift:
    pooled_n: int
    pooled_distribution: list[float]
    per_site_psi: dict[str, float]
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FederatedCalibration:
    pooled_n: int
    pooled_ece: float
    per_site_ece: dict[str, float]
    per_site_n: dict[str, int]
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FederatedMoments:
    pooled_n: int
    pooled_mean: float
    pooled_std: float
    per_site_mean: dict[str, float]
    per_site_z: dict[str, float]
    severity: Literal["ok", "warn", "alert"]
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# -------------------------------------------------------------- aggregators


def _check_aligned_bins(stats: list[HistogramSummary] | list[CalibrationSummary]) -> None:
    if not stats:
        raise ValueError("at least one site summary is required")
    edges = stats[0].bin_edges
    for s in stats[1:]:
        if s.bin_edges != edges:
            raise ValueError(
                f"site {s.site_id}: bin_edges differ from {stats[0].site_id}; "
                "callers must align bin edges before federation",
            )


def aggregate_histograms(
    summaries: list[HistogramSummary],
    *,
    warn_psi: float = 0.10,
    alert_psi: float = 0.25,
) -> FederatedDrift:
    """Pool per-site histograms and compute per-site PSI vs the pool.

    Severity reflects the **maximum** per-site PSI: a site whose
    distribution is drifting away from the federation should be loud.
    """
    _check_aligned_bins(summaries)
    k = len(summaries[0].counts)
    pooled_counts = [0] * k
    for s in summaries:
        for i, c in enumerate(s.counts):
            pooled_counts[i] += c
    pooled_n = sum(pooled_counts)
    if pooled_n == 0:
        raise ValueError("pooled distribution is empty")
    pooled_dist = [c / pooled_n for c in pooled_counts]

    per_site: dict[str, float] = {}
    for s in summaries:
        if s.n == 0:
            per_site[s.site_id] = 0.0
            continue
        site_dist = [c / s.n for c in s.counts]
        per_site[s.site_id] = _psi(site_dist, pooled_dist)

    worst = max(per_site.values()) if per_site else 0.0
    if worst >= alert_psi:
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif worst >= warn_psi:
        sev = "warn"
    else:
        sev = "ok"

    return FederatedDrift(
        pooled_n=pooled_n,
        pooled_distribution=pooled_dist,
        per_site_psi=per_site,
        severity=sev,
        extra={"warn_psi": warn_psi, "alert_psi": alert_psi, "worst_psi": worst},
    )


def aggregate_calibration(
    summaries: list[CalibrationSummary],
    *,
    warn_ece: float = 0.05,
    alert_ece: float = 0.10,
) -> FederatedCalibration:
    """Pool per-site calibration bins into a single ECE.

    Each site's ECE is also reported so an outlier site can be spotted.
    """
    _check_aligned_bins(summaries)
    k = len(summaries[0].bin_count)
    pooled_count = [0] * k
    pooled_pos = [0] * k
    pooled_conf = [0.0] * k
    for s in summaries:
        for i in range(k):
            pooled_count[i] += s.bin_count[i]
            pooled_pos[i] += s.bin_pos[i]
            pooled_conf[i] += s.bin_conf_sum[i]
    pooled_n = sum(pooled_count)
    if pooled_n == 0:
        raise ValueError("pooled calibration is empty")
    pooled_ece = _ece(pooled_count, pooled_pos, pooled_conf, pooled_n)

    per_site_ece: dict[str, float] = {}
    per_site_n: dict[str, int] = {}
    for s in summaries:
        per_site_n[s.site_id] = s.n
        per_site_ece[s.site_id] = (
            _ece(s.bin_count, s.bin_pos, s.bin_conf_sum, s.n) if s.n else 0.0
        )

    if pooled_ece >= alert_ece:
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif pooled_ece >= warn_ece:
        sev = "warn"
    else:
        sev = "ok"

    return FederatedCalibration(
        pooled_n=pooled_n,
        pooled_ece=pooled_ece,
        per_site_ece=per_site_ece,
        per_site_n=per_site_n,
        severity=sev,
        extra={"warn_ece": warn_ece, "alert_ece": alert_ece},
    )


def aggregate_moments(
    summaries: list[MomentSummary],
    *,
    warn_z: float = 2.0,
    alert_z: float = 3.0,
) -> FederatedMoments:
    """Pool per-site moments into a global mean/std and compute per-site z-scores.

    The per-site z-score is ``(site_mean - pooled_mean) / pooled_std`` and
    flags sites that have drifted away from the federation's centre of mass.
    """
    if not summaries:
        raise ValueError("at least one site summary is required")
    pooled_n = sum(s.n for s in summaries)
    if pooled_n <= 0:
        raise ValueError("pooled n must be > 0")
    pooled_sum = sum(s.sum for s in summaries)
    pooled_sum_sq = sum(s.sum_sq for s in summaries)
    pooled_mean = pooled_sum / pooled_n
    var = max(0.0, pooled_sum_sq / pooled_n - pooled_mean ** 2)
    pooled_std = math.sqrt(var)

    per_site_mean: dict[str, float] = {}
    per_site_z: dict[str, float] = {}
    worst = 0.0
    for s in summaries:
        m = s.sum / s.n if s.n else 0.0
        per_site_mean[s.site_id] = m
        z = abs(m - pooled_mean) / pooled_std if pooled_std > 0 else 0.0
        per_site_z[s.site_id] = z
        worst = max(worst, z)

    if worst >= alert_z:
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif worst >= warn_z:
        sev = "warn"
    else:
        sev = "ok"

    return FederatedMoments(
        pooled_n=pooled_n,
        pooled_mean=pooled_mean,
        pooled_std=pooled_std,
        per_site_mean=per_site_mean,
        per_site_z=per_site_z,
        severity=sev,
        extra={"warn_z": warn_z, "alert_z": alert_z, "worst_z": worst},
    )


# ------------------------------------------------------------------ helpers


def _psi(p: list[float], q: list[float], eps: float = 1e-6) -> float:
    """PSI(p || q) with the standard small-bin smoothing."""
    total = 0.0
    for pi, qi in zip(p, q, strict=False):
        a = max(pi, eps)
        b = max(qi, eps)
        total += (a - b) * math.log(a / b)
    return total


def _ece(
    bin_count: list[int], bin_pos: list[int], bin_conf_sum: list[float], n: int,
) -> float:
    if n <= 0:
        return 0.0
    total = 0.0
    for c, p, cs in zip(bin_count, bin_pos, bin_conf_sum, strict=False):
        if c == 0:
            continue
        acc = p / c
        conf = cs / c
        total += (c / n) * abs(acc - conf)
    return total
