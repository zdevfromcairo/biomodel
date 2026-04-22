# Security & Privacy Checklist

BioModel Monitor is designed to operate in healthcare-grade environments.
This checklist applies to Phase 1 (offline batch monitor) deployments.

## PHI handling
- [ ] No raw PHI fields are required by the canonical schema.
- [ ] Patient identifiers, when supplied, must be salted-hashed upstream
  (`PatientDemographics.patient_hash`).
- [ ] `inputs_ref` carries an opaque URI / hash only; the platform never
  pulls the raw input artifact.
- [ ] Demographics are coarse: age band (e.g. `40-49`), not date of birth.
- [ ] Free-text fields (`notes`) must be reviewed by the vendor before
  being submitted; the platform does not strip PHI from free text.

## Storage
- [ ] Default deployment is on-prem or single-tenant cloud inside the
  vendor's perimeter.
- [ ] Baselines and reports are written to a vendor-controlled directory
  (`output_dir`, `baseline.path`).
- [ ] No outbound network calls are made by the core pipeline.
- [ ] SQLite / JSON files used for storage in Phase 1 are owned by the
  vendor's filesystem ACLs.

## Access control
- [ ] Reports and JSON bundles inherit the host's filesystem permissions.
- [ ] CLI runs under a service account with least-privilege access to the
  batch and baseline directories.
- [ ] The Streamlit dashboard, when used, must be deployed behind the
  vendor's SSO / reverse proxy. The bundled app does not authenticate.

## Audit log
- [ ] Each report includes batch id, model id, model version, generation
  timestamp, and the exact alert configuration used.
- [ ] JSON bundle is the canonical machine-readable audit artifact;
  retain per the vendor's retention policy.
- [ ] CLI invocations should be captured by the host's job scheduler logs
  (cron / systemd / Argo / Airflow).

## Data retention
- [ ] Reports and JSON bundles: retain for the duration required by the
  vendor's QMS or regulator (typically ≥ 5 years for medical devices).
- [ ] Baselines: retain at least one approved baseline per model version
  for the supported lifetime of that version.
- [ ] Raw batch files: lifetime governed by the vendor; the platform does
  not require retention.

## Cryptography
- [ ] No secrets are stored in the repository or in baselines.
- [ ] If batch files are encrypted at rest, decryption happens upstream;
  the platform reads plaintext from the local filesystem.

## Supply chain
- [ ] Dependencies are pinned by the vendor's environment management
  process.
- [ ] CI runs lint and tests on every PR (see `.github/workflows/ci.yml`).

## Responsible disclosure
- [ ] Security issues should be reported privately to the maintainers.
  Coordinated disclosure timelines follow industry norms (90 days).
