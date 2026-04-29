# Changelog

## v0.6.0 — Streaming, Forecasting & SDK

### Added
- **Streaming micro-batching** (`streaming/buffer.py`) — thread-safe
  `WindowBuffer` that groups records by `(model_id, model_version)` and
  emits a `PredictionBatch` whenever its size or age threshold is crossed.
- **Server `/ingest` endpoint** — push records into the in-memory window
  from any number of producers; the pipeline runs automatically on flush.
  `flush=true` forces an end-of-day drain.
- **Drift forecasting** (`metrics/forecast.py`) — Holt's linear method with
  in-sample residual band; reports an explicit *ETA-to-breach* and severity
  (`ok`/`warn`/`alert`) given a threshold and direction.
- **Server `/forecast` endpoint** — runs the forecaster against any stored
  metric history.
- **Conformal prediction** (`metrics/conformal.py`) — split-conformal
  calibration with the standard finite-sample correction, plus an
  `empirical_coverage` monitor that flags coverage drift.
- **Concept-drift detector** (`metrics/concept_drift.py`) — PCA
  reconstruction-error baseline + scoring; catches multivariate shifts that
  univariate tests miss.
- **Interaction-effect attribution** (`intelligence/causal.py`) — ranks
  joint `(dim1=v1, dim2=v2)` subgroups by their *lift* over the additive
  marginal expectation.
- **Python SDK** (`client/`) — `BioModelMonitorClient`, stdlib-only
  (urllib), injectable transport for testing, full coverage of the v0.4–v0.6
  API surface.
- **CLIs**: `biomodel-monitor forecast`, `ingest`, `client-call`.

### Changed
- `AppSettings` gains `stream_max_records` and `stream_max_age_s`.
- `create_app` accepts an optional `batch_runner` (in addition to the
  existing `pipeline_runner`) so the streaming endpoint can drive the
  pipeline against an in-memory `PredictionBatch`.

## v0.5.0 — Intelligence & Explanation

### Added
- **Root-cause attribution** (`intelligence/attribution.py`) — for any alert,
  rank which dimension/value contributed most. Score is
  `|delta_from_pooled_mean| × share_of_records` with a small-N guard.
- **Changepoint detection** (`intelligence/changepoint.py`) — divisive
  segmentation with a CUSUM-style mean-shift score. Pinpoints *when* drift
  began, not just that it's there.
- **Counterfactual drift / "what-if"** (`intelligence/whatif.py`) — recompute
  output drift after dropping records that match `dim=value` filters.
- **Robust z-score anomaly** (`intelligence/anomaly.py`) — median / MAD-based
  anomaly score layered onto every metric history.
- **Active-learning incident queue** (`intelligence/active_learning.py`) —
  rank open incidents by expected information gain so labeling effort is
  spent where it most improves threshold tuning.
- **Auto model card** (`intelligence/modelcard.py`) — generate a publishable
  Markdown model card from what's already in the store.
- **CLI**: `biomodel-monitor explain`, `whatif`, `model-card`.

### Changed
- New `intelligence` subpackage; surface re-exports via
  `biomodel_monitor.intelligence`.

## v0.4.0 — Server & Stack

### Added
- **FastAPI HTTP server** (`server/`) — REST endpoints for runs, alerts,
  annotations, incidents, baselines, metric history, model card, what-if,
  and changepoint analysis. OpenAPI at `/docs`.
- **API-key auth** with `X-API-Key` header; multiple keys allowed; CORS
  origins configurable via `--cors` or `BIOMODEL_CORS`.
- **Structured JSON access logs** for every request (ts, method, path,
  status, duration_ms).
- **Prometheus `/metrics`** endpoint, zero external dependency. Counters and
  histograms for HTTP traffic and alert emission.
- **Storage Protocol** (`store/backend.py`) decoupling the rest of the system
  from the concrete adapter.
- **Postgres adapter** (`store/postgres.py`) implementing the same `Storage`
  Protocol as SQLite, with a `?` → `%s` placeholder shim so the existing SQL
  is reused verbatim.
- **Notification channels**: `SlackChannel`, `PagerDutyChannel` (Events API
  v2 with severity mapping and `dedup_key`), `TeamsChannel` (MessageCard).
- **HMAC-SHA256 signed webhooks** (`X-BioModel-Timestamp`, `X-BioModel-Signature: v1=…`).
- **Exponential-backoff retry** in the webhook base class.
- **Dockerfile + docker-compose.yml** spinning up `server` + `watcher` +
  `dashboard` against a shared volume.
- **CLI**: `biomodel-monitor serve`.

### Changed
- `pyproject.toml` now offers `server`, `postgres`, and `docs` extras.

## v0.3.0 — Beyond pathology + advanced statistics

### Added
- **Multi-class calibration** — top-label ECE, class-wise (one-vs-rest) ECE,
  and multiclass Brier (`metrics/calibration_multiclass.py`).
- **Bootstrap confidence intervals** — one- and two-sample non-parametric CIs
  with deterministic seeding (`metrics/bootstrap.py`).
- **Fairness metrics** — demographic-parity, equal-opportunity, and
  equalized-odds gaps with per-group breakdown and small-N guards
  (`metrics/fairness.py`).
- **Radiology rule pack** — laterality consistency, anatomy prior
  (region-in-FOV), modality cross-checks (`metrics/domain_rules.py`).
- **Multimodal omics rule pack** — expression bounds, pathway sign-consistency
  (`metrics/domain_rules.py`).
- **Cross-model dependency graph** — declare upstream→downstream edges,
  attribute downstream alerts to upstream regressions (`dependency/graph.py`).
- **Regulatory / QMS export bundle** — copies the report set, embeds the
  alert + annotation audit trail, and writes a SHA-256 manifest;
  `verify-bundle` detects post-hoc edits (`reports/regulatory.py`).
- CLI: `export-bundle`, `verify-bundle`.

## v0.2.0 — Operational monitoring

### Added
- **Persistent SQLite metrics + incident store** — batches, runs, alerts,
  annotations, headline metrics, baselines (`store/repository.py`).
- **Persistence-aware severity** — alert scores are escalated when a key
  recurs across recent runs.
- **Incident workspace** — ack / resolve / comment / label
  (`tp` / `fp` / `needs_review`), with a durable audit trail
  (`incidents/workspace.py`).
- **Threshold auto-tuning** — read labeled history and propose per-category
  thresholds at a target recall (`alerts/threshold_tuner.py`).
- **Rolling baseline learner with explicit promotion** — per cohort and per
  site; promotion is intentionally manual (`baselines/learner.py`).
- **Near-real-time ingestion** — polling `DirectoryWatcher` + at-least-once
  `FilesystemQueue` (`scheduler/watcher.py`).
- **Pluggable notification channels** — file (JSON-Lines), webhook
  (caller-supplied transport), email (caller-supplied SMTP delivery)
  (`notifications/channels.py`).
- CLI: `baseline-update`, `baseline-promote`, `incidents list/annotate`,
  `tune-thresholds`, `watch`, `process-queue`.

### Changed
- `run_pipeline` now optionally takes a `MetricsStore`; when supplied it
  persists the run and rescales alert scores by historical persistence.
- `BatchMetadata.source` is now propagated into the persistent store.

## v0.1.0 — Phase 1
- Initial offline batch monitor: schema, ingest, drift / calibration /
  subgroup / plausibility / silent-failure metrics, alert engine, HTML +
  Markdown reports, Streamlit dashboard, CLI, pathology example.
