# Roadmap

## Phase 1 — Offline batch monitor (this release)
- Schema + ingest + validation.
- Drift (PSI, KS, JS, MMD, cosine).
- Calibration (ECE, MCE, Brier, reliability).
- Subgroup slicing with Wilson CI and small-N guard.
- Pathology plausibility rule registry.
- Silent-failure signatures.
- Alert engine (severity, dedup, root-cause hint).
- HTML + Markdown reports + JSON metrics bundle.
- Streamlit dashboard.
- CLI (`biomodel-monitor run --config`).
- Sample pathology pipeline integration.

## Phase 2 — Operational monitoring
- Near-real-time ingestion (file-watch + queue connector).
- Incident review workspace: annotate alerts, attach notes, mark resolved.
- Threshold tuning from historical false-positive / true-positive rates.
- Site-specific baselines learned over rolling windows, with explicit
  "approved baseline" promotion.
- Persistent metrics store (Postgres) for cross-batch trend views.
- Notification channels (email, Slack, webhook).

## Phase 3 — Beyond pathology
- Radiology-flavored plausibility rules (laterality consistency,
  anatomical region priors, modality cross-checks).
- Multimodal omics-adjacent rules (pathway plausibility, marker panels).
- Cross-model dependency graphs (an upstream segmenter regression
  surfaced as a downstream classifier alert).
- Regulatory export packs (model performance reports formatted for QMS
  ingestion).

## Non-goals (forever)
- Replacing a clinical-grade QMS.
- Automatically retraining or recalibrating models.
- Acting as a primary inference platform.
