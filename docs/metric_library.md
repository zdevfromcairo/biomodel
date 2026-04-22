# Metric Library

Each metric has: a definition, when it fires, how to interpret it, and a
reference. Severities follow ``ok`` / ``warn`` / ``alert``.

## Drift

### PSI — Population Stability Index
Definition: `Σ (current_pct − ref_pct) · log(current_pct / ref_pct)`,
computed over quantile bins of the reference.
Fires on continuous-feature or score distribution shift.
Interpretation: < 0.10 stable; 0.10–0.25 moderate (warn); > 0.25 large (alert).
Reference: Wu, Olson (2010); industry standard in credit scoring.

### KS — Kolmogorov-Smirnov two-sample test
Definition: `max |F_ref(x) − F_cur(x)|`. Reports stat and p-value.
Fires when continuous distributions differ.
Interpretation: severity is driven by p-value (warn < 0.05, alert < 0.001).

### JS divergence (categorical)
Definition: Jensen-Shannon divergence in base 2 between two empirical PMFs.
Bounded in [0, 1].
Fires when categorical metadata distributions (site, scanner, stain,
tissue) shift.
Interpretation: warn ≥ 0.05; alert ≥ 0.15.

### MMD with RBF kernel
Definition: unbiased squared MMD using RBF kernel with median-heuristic
bandwidth, plus a permutation p-value.
Fires when high-dimensional distributions (e.g. embeddings) differ.
Interpretation: warn p < 0.05; alert p < 0.01.

### Cosine shift of mean embedding
Definition: 1 − cos(mean(ref), mean(cur)).
Fires when the centroid of an embedding distribution moves.

## Calibration

### ECE — Expected Calibration Error
Definition: weighted mean absolute gap between bin confidence and bin
accuracy. Supports uniform and quantile binning.
Fires when predicted probabilities are not aligned with empirical risk.
Interpretation: warn ≥ 0.05; alert ≥ 0.10.

### MCE — Maximum Calibration Error
Definition: max bin-level confidence/accuracy gap.
Fires when at least one confidence band is severely miscalibrated.
Interpretation: warn ≥ 0.10; alert ≥ 0.25.

### Brier score
Definition: mean squared error between score and binary label.
Fires on miscalibration and discrimination loss combined.

## Subgroup degradation

### slice_metrics
Splits records by a metadata dimension, computes the chosen metric per
slice, reports delta vs. global, includes Wilson confidence intervals,
and skips alerts on slices below ``min_n``.
Severity: warn at |Δ| ≥ 0.05, alert at |Δ| ≥ 0.10 (with min_n satisfied).

## Plausibility (pathology starter rules)

### tumor_probability_vs_tissue_area
Tumor score must not exceed the slide's tissue-area fraction.

### mitosis_count_vs_field_area
Mitotic density must not exceed a biological cap (default 200 / mm²).

### mutually_exclusive_markers
Two markers declared mutually exclusive must not both be strongly positive.

### slide_tile_consistency
Slide-level prediction must agree with mean tile-level prediction within tolerance.

## Silent failure signatures

### entropy_collapse
Mean binary entropy of predicted scores. Either compared to a baseline
(relative drop) or evaluated absolutely.
Fires when predictions become unnaturally certain.

### confidence_accuracy_decoupling
Compares accuracy at the high-confidence tail (`s ≥ 0.9` or `s ≤ 0.1`) to
overall accuracy. Well-behaved models are *more* accurate when confident.
A flat or inverted gap is a silent-failure signature.

### prediction_drift_without_input_drift
Output distribution moved but input/modality distributions did not — a
strong indicator of an upstream preprocessing or pipeline change.
