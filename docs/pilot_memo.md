# Pilot Memo Template

## Pilot summary
- **Vendor / partner:** _______
- **Model under monitoring:** _______ (id / version)
- **Indication / task:** _______
- **Sites in scope:** _______
- **Scanners / protocols in scope:** _______
- **Pilot duration:** _______ weeks
- **Decision criterion:** see "Success criteria" below.

## Goals
1. Confirm BioModel Monitor detects a synthetic site shift planted in
   shadow data within one batch.
2. Demonstrate per-cohort calibration drift detection on retrospective
   data.
3. Produce a reviewer-ready report that the vendor's clinical lead can
   sign off on without engineering translation.

## In scope
- Ingestion of one weekly batch (CSV/JSONL/Parquet) with the canonical
  schema.
- Drift, calibration, subgroup, plausibility, silent-failure metrics.
- HTML + Markdown reports per batch.
- A reference (baseline) window built from a vendor-supplied historical
  batch.
- Up to three vendor-specific plausibility rules contributed via the rule
  registry.

## Out of scope
- Real-time / streaming ingestion.
- Direct integration with EHR or LIS.
- Automatic model retraining.
- PHI handling — the vendor pre-hashes / pre-redacts identifiers.

## Success criteria
- Detection of the planted site-shift in ≤ 1 batch.
- Zero false-positive plausibility alerts on the clean reference batch.
- Report rated "useful for clinical review" by the vendor's clinical lead
  on a 1–5 scale, ≥ 4.

## Roles
- **Vendor ML lead:** owns batch export, baseline approval.
- **Vendor clinical lead:** reviews reports, signs off on success criteria.
- **BioModel Monitor team:** runs the platform, ships the rule plugins,
  attends weekly review.

## Deliverables
- Weekly Markdown + HTML report.
- JSON metrics bundle for archival.
- Pilot retrospective: detection coverage, false-positive rate, reviewer
  feedback, recommended next step (expand / extend / discontinue).
