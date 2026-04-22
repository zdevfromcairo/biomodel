# BioModel Monitor — Product Spec (Phase 1)

## One-sentence product
BioModel Monitor is a post-deployment monitoring and assurance platform for
multimodal medical AI that detects dataset shift, biological-plausibility
failures, calibration drift, and cohort-specific degradation.

## Problem
Generic ML monitoring tools track uptime and aggregate accuracy. They were
built for SaaS classifiers, not for medical AI. Medical models can appear
stable in aggregate while quietly becoming biologically implausible,
clinically miscalibrated, or distributionally brittle across sites,
scanners, stains, protocols, and populations. There is no specialized
monitoring layer that speaks the language of biomedical model risk.

## Users
- Digital pathology AI vendors operating across multiple labs and scanners.
- Multimodal healthcare AI startups deploying into hospital partners.
- Hospital innovation teams piloting external models.
- Biotech teams moving research models into operational use.

## Phase 1 scope
Offline batch monitoring. Given a batch of predictions plus per-record
metadata, the system computes drift, calibration, subgroup, plausibility,
and silent-failure metrics; raises alerts; and produces an HTML + Markdown
report and a JSON metrics bundle.

## Out of Phase 1 (deferred)
- Near-real-time / streaming ingestion.
- Incident-review workspace with annotations and tickets.
- Auto-tuned alert thresholds learned from historical data.
- Per-site baseline learning loops.

## MVP workflows
1. Ingest a batch of predictions + metadata (CSV / JSONL / Parquet).
2. Validate the schema and (optionally) the registered model contract.
3. Compute drift vs. a baseline reference window.
4. Compute calibration overall and per cohort.
5. Slice metrics by site / scanner / stain / tissue / cohort.
6. Run pluggable plausibility rules against record-level metadata.
7. Run silent-failure signature checks.
8. Emit deduplicated, severity-scored alerts with root-cause hints.
9. Render HTML + Markdown reports and a JSON bundle for the dashboard.

## Success criterion
A model owner reading the report can answer:

> Is my model still behaving acceptably, and if not, exactly where is it
> breaking?

## Non-goals (Phase 1)
- Replacing a clinical-grade QMS.
- Re-training or re-calibrating models automatically.
- Performing primary statistical hypothesis testing for regulatory submissions.

## Architecture
See [the metric library](metric_library.md) for the supported metrics and
[the roadmap](roadmap.md) for what comes after Phase 1.
