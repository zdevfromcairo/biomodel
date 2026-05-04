# Changelog

## v0.9.0 — Platform & Governance

### Added
- **Model registry + lineage** (`registry/`) — SQLite-backed catalogue of
  `(model_id, model_version)` records with `status`
  (`active`/`quarantined`/`retired`), training-data hash, framework, notes
  and a directed `lineage` table. Thread-safe (lock-protected) so the
  FastAPI worker pool can share a single registry. Endpoints:
  `GET/POST /models`, `GET /models/{id}/{ver}`,
  `POST /models/{id}/{ver}/quarantine`, `…/unquarantine`,
  `POST /lineage`, `GET /lineage/{id}/{ver}`.
- **Declarative governance policies** (`policy/`) — load YAML rules and
  evaluate them against alerts to produce typed `PolicyAction`
  (`notify` / `quarantine` / `promote`). Endpoint `POST /policy/evaluate`,
  CLI `biomodel-monitor policy-eval`.
- **Differential-privacy federation** (`federated/dp.py`) — Laplace
  mechanism with `PrivacyAccountant` for sequential ε-composition;
  `privatise_histogram` and `privatise_mean` helpers; non-negative
  post-processing on counts.
- **Sliced 1-D Wasserstein** (`metrics/wasserstein.py`) — closed-form 1-D
  W₁ for univariate inputs, random-projection sliced W₁ for multivariate
  embeddings; severity bands. Endpoint `POST /wasserstein`, CLI
  `biomodel-monitor wasserstein`.
- **Canary deployments via mSPRT** (`canary/`) — mixture sequential
  probability ratio test (Howard et al. 2021), peek-as-often-as-you-like
  Type-I control, returns `promote` / `rollback` / `inconclusive`. CLI
  `biomodel-monitor canary`.
- **SBOM + SLSA provenance** (`security/sbom.py`) —
  CycloneDX-1.5 lite SBOM (`build_sbom`) and in-toto SLSA-v1.0 provenance
  with SHA-256 subject digests (`build_provenance`). Endpoint `GET /sbom`,
  CLI `biomodel-monitor sbom`.
- **Architecture Decision Records** under `docs/adr/` — five MADR-lite ADRs
  documenting the durable design choices (severity contract, SQLite
  default, opt-in tenancy, registry & quarantine).
- **Tenant permissions extended** with `register_model`, `add_lineage`
  (writer) and `quarantine_model`, `unquarantine_model` (admin).

### Changed
- Python SDK `User-Agent` bumped to `biomodel-monitor-sdk/0.9`.
- Server `AppSettings` gains `registry_path` and `policy_path`
  (env vars `BIOMODEL_REGISTRY_PATH`, `BIOMODEL_POLICY_PATH`).
- New docs page *Platform & Governance (v0.9)* and an ADR index.

### Tested
- 260 tests passing (was 235), including 17 new unit tests for the
  v0.9 modules and 8 new integration tests for the new endpoints.

## v0.8.0 — Reactive core

### Added
- **Concurrent batch pipeline** (`pipeline_async.py`) — `run_pipeline_async()`
  schedules drift / calibration / subgroup / plausibility on a thread pool and
  produces byte-identical results to the synchronous pipeline (same alert keys,
  metric rows, run id). Returns an `AsyncPipelineStats` block with per-phase
  wall time. CLI: `biomodel-monitor pipeline-async`.
- **Live event bus + WebSocket stream** (`server/events.py`,
  `/ws/events`, `/events`) — in-process pub/sub that broadcasts
  `alert.emitted`, `run.completed`, `incident.annotated`,
  `baseline.promoted`, `ingest.flushed` and `system.info` events. Bounded
  queues and history; slow consumers drop the *oldest* events so the
  publisher is never blocked. SDK helpers `client.events()` and
  `client.stream_events()`.
- **Embedding-drift via Maximum Mean Discrepancy** (`metrics/embedding_drift.py`)
  — RBF kernel with median-heuristic bandwidth, unbiased MMD² estimator,
  permutation p-value with Phipson–Smyth correction. Endpoint `POST /mmd` and
  CLI `biomodel-monitor mmd`.
- **Online CUSUM change detector** (`metrics/cusum.py`) — Page's two-sided
  CUSUM (`CUSUMMonitor`) and a one-shot `cusum_offline` helper that estimates
  target/sigma from a reference window when omitted. Endpoint `POST /cusum`
  and CLI `biomodel-monitor cusum`.
- **Per-record local attribution** (`intelligence/local_attribution.py`) —
  closed-form leave-one-out influence per record for `score_mean` and
  `positive_rate`, plus a `aggregate_top_dimensions` roll-up. Severity
  reflects how concentrated the contribution mass is.
- **Drift influence graph** (`intelligence/drift_graph.py`) — directed
  graph of `(dimension, value) → target dimension` JS-divergence weights,
  exportable as Graphviz `dot` source. CLI: `biomodel-monitor drift-graph`.
- **OpenAPI as YAML** at `GET /openapi.yaml` for downstream codegen.
- **MkDocs custom hero + SVG logo** under `docs/assets/` and a new
  *Reactive core (v0.8)* page (`docs/reactive.md`).

### Changed
- Server now publishes events from `trigger_run`, `ingest`, `annotate` and
  `promote_baseline` to the new event bus.
- Python SDK bumps `User-Agent` to `biomodel-monitor-sdk/0.8` and gains
  `mmd()`, `cusum()`, `events()` and `stream_events()` methods.

### Tested
- 235 tests passing (was 204), including 18 new metric/intelligence tests
  and 13 new endpoint/pipeline-async tests.

## v0.7.0 — Multi-tenant, Plugins & Federation

### Added
- **Multi-tenancy + RBAC** (`tenancy/`) — every API key now maps to a
  `(tenant_id, role)` pair (`viewer` / `operator` / `writer` / `admin`).
  Endpoints that mutate state call `tenant.require(action)` and return 403
  if the role is insufficient. Backwards-compatible: when no tenant
  registry is configured, the server keeps the v0.4 behaviour.
- **Plugin system** (`plugins/`) — third-party `metrics`, `notifiers` and
  `loaders` are auto-discovered through Python entry points. Failures to
  load a plugin are logged and skipped, never fatal. New `/plugins`
  endpoint and `biomodel-monitor plugins list` CLI.
- **Federated aggregation** (`federated/aggregator.py`) — pool drift,
  calibration and continuous-metric statistics across sites *without
  sharing raw records*. Per-site PSI / ECE / z-score is also reported so
  outliers stand out. New `/federate/drift` and `/federate/calibration`
  endpoints, and a `biomodel-monitor federate` CLI.
- **Tamper-evident audit log** (`audit/log.py`) — append-only JSONL with
  SHA-256 hash chaining; `/audit/verify` re-walks the chain and reports
  the first broken link. Every annotation and baseline promotion is
  recorded automatically.
- **Predictive uncertainty** (`metrics/uncertainty.py`) — predictive
  entropy + BALD mutual information, with a batch summary that follows
  the project's `severity` contract.
- **Helm chart** under `deploy/helm/biomodel-monitor/` — Chart.yaml,
  values.yaml, deployment + service + secret templates so Kubernetes
  operators can `helm install` the server straight from the repo.
- **Project polish** — `CONTRIBUTING.md`, `GOVERNANCE.md`, redesigned
  homepage with a live feature matrix.

### Changed
- `AppSettings` gains `tenant_keys`, `audit_log_path`, `discover_plugins`.
- Mutating endpoints (`/alerts/*/annotations`, `/baselines/*/promote`)
  now require the corresponding role *and* are recorded in the audit log
  when one is configured.

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
