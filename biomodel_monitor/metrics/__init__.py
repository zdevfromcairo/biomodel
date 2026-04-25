"""Drift, calibration, subgroup, plausibility, fairness, and silent-failure metrics."""

from biomodel_monitor.metrics.bootstrap import BootstrapCI, bootstrap_ci, two_sample_bootstrap_ci
from biomodel_monitor.metrics.calibration import (
    CalibrationResult,
    brier_score,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_curve,
)
from biomodel_monitor.metrics.calibration_multiclass import (
    MulticlassCalibrationResult,
    class_wise_ece,
    multiclass_brier,
    top_label_ece,
)
from biomodel_monitor.metrics.domain_rules import (
    anatomy_prior,
    builtin_omics_rules,
    builtin_radiology_rules,
    expression_bounds,
    laterality_consistency,
    modality_cross_check,
    pathway_consistency,
)
from biomodel_monitor.metrics.drift import (
    DriftResult,
    embedding_cosine_shift,
    js_divergence_categorical,
    ks_test,
    mmd_rbf,
    psi,
)
from biomodel_monitor.metrics.fairness import (
    FairnessResult,
    demographic_parity,
    equal_opportunity,
    equalized_odds,
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
    # drift
    "DriftResult", "js_divergence_categorical", "ks_test", "mmd_rbf", "psi",
    "embedding_cosine_shift",
    # calibration (binary)
    "CalibrationResult", "brier_score", "expected_calibration_error",
    "maximum_calibration_error", "reliability_curve",
    # calibration (multiclass)
    "MulticlassCalibrationResult", "class_wise_ece", "multiclass_brier", "top_label_ece",
    # subgroup
    "SubgroupResult", "slice_metrics", "wilson_interval",
    # fairness
    "FairnessResult", "demographic_parity", "equal_opportunity", "equalized_odds",
    # plausibility
    "PlausibilityViolation", "PlausibilityRule", "RuleRegistry",
    "builtin_pathology_rules", "builtin_radiology_rules", "builtin_omics_rules",
    "anatomy_prior", "expression_bounds", "laterality_consistency",
    "modality_cross_check", "pathway_consistency",
    # silent failure
    "SilentFailureResult", "confidence_accuracy_decoupling",
    "entropy_collapse", "prediction_drift_without_input_drift",
    # bootstrap
    "BootstrapCI", "bootstrap_ci", "two_sample_bootstrap_ci",
]
