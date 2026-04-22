# Competition Analysis

| Category | Examples | Why they don't suffice for medical AI |
|---|---|---|
| Generic ML monitoring | Evidently, Arize, WhyLabs, Fiddler | Modality-blind. They drift on tabular features and predictions but have no concept of stain, scanner, protocol, tissue type, or cohort. They lack any plausibility layer. |
| Healthcare model governance | MDR/IVDR documentation tooling, SaMD QMS suites | Built for documentation, audit trail, and change management. They do not run live drift / calibration / plausibility computations on production batches. |
| Internal QA dashboards | Vendor-built notebooks and BI dashboards | Bespoke, brittle, and not portable. Maintained by a single ML engineer, not auditable by a clinical or regulatory reviewer, and rarely include subgroup or plausibility analysis. |

## Why generic ML monitoring fails for medical AI
1. **Not modality aware.** Medical AI failures are usually traceable to a
   site, scanner model, stain protocol, magnification, or sample prep
   change. Generic tools track features and predictions but ignore
   modality metadata, so they detect "something drifted" without telling
   the operator *what* and *where*.
2. **No biological plausibility layer.** Generic tools have no notion that
   tumor probability cannot exceed tissue area, that some markers are
   mutually exclusive, or that slide-level and tile-level predictions
   should agree. These are the failures clinicians and pathologists care
   about most.
3. **Under-serve scientific and audit workflows.** Healthcare buyers
   expect reports a clinician can read, an audit trail a regulator will
   accept, and a per-cohort breakdown a pathologist can act on. Generic
   monitoring delivers SaaS-style dashboards that don't fit either review
   channel.

## Where BioModel Monitor wins
- Medical-AI-native schema: site, scanner, stain, tissue, cohort, hashed demographics.
- Pluggable plausibility rule registry with pathology starters.
- Per-cohort calibration + per-slice severity with small-N safeguards.
- Silent-failure signatures specifically tuned for medical-AI failure modes.
- Reports written for scientific reviewers, not just MLOps engineers.
