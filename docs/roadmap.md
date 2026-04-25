# Roadmap

## Phase 1 — Offline batch monitor (v0.1)
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

## Phase 2 — Operational monitoring (v0.2 — shipped)
- Near-real-time ingestion: directory watcher + at-least-once filesystem queue
  (`biomodel-monitor watch` / `process-queue`).
- **Persistent metrics store** (SQLite, single file, on-prem friendly) — batches,
  runs, alerts, audit-trail annotations, headline metrics, baselines.
- **Persistence-aware severity**: alert scores are escalated when a key
  recurs across recent runs.
- **Incident workspace**: ack, resolve, comment, label (`tp`/`fp`/`needs_review`)
  with a durable audit trail (`biomodel-monitor incidents`).
- **Threshold auto-tuning** from labeled history
  (`biomodel-monitor tune-thresholds`).
- **Rolling per-site / per-cohort baselines** with explicit human promotion
  (`baseline-update` / `baseline-promote`). Auto-promotion is intentionally
  not provided — that is how silent failures get laundered into the new normal.
- Pluggable notification channels (file, webhook, email — transport injected).

## Phase 3 — Beyond pathology + advanced statistics (v0.3 — shipped)
- **Multi-class calibration**: top-label ECE, class-wise (one-vs-rest) ECE,
  multiclass Brier.
- **Bootstrap confidence intervals** (one- and two-sample) for any scalar
  statistic, with deterministic seeding.
- **Fairness metrics**: demographic-parity gap, equal-opportunity gap,
  equalized-odds gap, with per-group breakdown and small-N guards.
- **Radiology plausibility rule pack**: laterality consistency, anatomy prior
  (region-in-FOV), modality cross-checks.
- **Multimodal omics rule pack**: expression-bound plausibility, pathway
  sign-consistency.
- **Cross-model dependency graph**: declare upstream→downstream edges, then
  attribute downstream alerts to upstream regressions.
- **Regulatory / QMS export pack**: signed (SHA-256 manifest), versioned bundles
  combining the report, audit trail, and a reproducible fingerprint
  (`export-bundle` / `verify-bundle`).

## Non-goals (forever)
- Replacing a clinical-grade QMS.
- Automatically retraining or recalibrating models.
- Acting as a primary inference platform.
- Auto-promoting baselines without a human decision.
