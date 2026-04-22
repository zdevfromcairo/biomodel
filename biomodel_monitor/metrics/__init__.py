"""Drift, calibration, subgroup, plausibility, and silent-failure metrics."""

from biomodel_monitor.metrics.calibration import (
    CalibrationResult,
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_curve,
)
from biomodel_monitor.metrics.drift import (
    DriftResult,
    embedding_cosine_shift,
    js_divergence_categorical,
    ks_test,
    mmd_rbf,
    psi,
)
from biomodel_monitor.metrics.plausibility import (
    PlausibilityRule,
    PlausibilityViolation,
    RuleRegistry,
    builtin_pathology_rules,
)
from biomodel_monitor.metrics.silent_failure import (
    SilentFailureResult,
    confidence_accuracy_decoupling,
    entropy_collapse,
    prediction_drift_without_input_drift,
)
from biomodel_monitor.metrics.subgroup import (
    SubgroupResult,
    slice_metrics,
    wilson_interval,
)

__all__ = [
    "DriftResult",
    "js_divergence_categorical",
    "ks_test",
    "mmd_rbf",
    "psi",
    "embedding_cosine_shift",
    "CalibrationResult",
    "brier_score",
    "expected_calibration_error",
    "maximum_calibration_error",
    "reliability_curve",
    "SubgroupResult",
    "slice_metrics",
    "wilson_interval",
    "PlausibilityViolation",
    "PlausibilityRule",
    "RuleRegistry",
    "builtin_pathology_rules",
    "SilentFailureResult",
    "confidence_accuracy_decoupling",
    "entropy_collapse",
    "prediction_drift_without_input_drift",
]
