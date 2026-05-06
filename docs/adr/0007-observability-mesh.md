# 0007 — Observability mesh & multi-modal as v0.11 capability

* **Status:** Accepted (v0.11)

## Context

Through v0.10 the platform exposed Prometheus metrics, a WebSocket event
bus and structured JSON logs — enough to run alongside any monitoring
stack, but not enough to participate in a **distributed** observability
stack. Modern operators expect a service of any size to:

* emit OpenTelemetry **traces** so the same span IDs that flow through
  their inference, feature pipeline and DB show up here too;
* emit OpenTelemetry **metrics** so they can be aggregated against the
  same SLOs;
* be honest about which **modality** an alert is about (the
  modality-agnostic drift detectors silently lump grayscale-vs-RGB
  changes in with intensity drift, which is information-destroying);
* answer "*which historical case is this most like?*" with a fast,
  auditable nearest-neighbour query;
* prove cryptographically that the model **weights** in production are
  the ones that were signed off on.

## Decision

Add four modules under
``biomodel_monitor/{observability,modality,vector,fingerprint}/``:

* `observability/` — Helpers (`init_otel`, `span`, `counter`,
  `histogram`, `status`) that wrap the OpenTelemetry API package when
  `BIOMODEL_OTEL` is on **and** the package is installed; otherwise they
  are free no-ops. Only the `opentelemetry-api` package is referenced
  (the SDK + exporters are the operator's choice). This is the same
  opt-in pattern used for v0.7 multi-tenancy and v0.9 governance.
* `modality/` — `check_image` / `check_text` / `check_tabular` with a
  `check_modality` dispatcher, all consuming **lightweight summary
  stats** (no Pillow / NLTK / transformers dependency). A categorical
  change like `channels: 3 → 1` is treated as `severity=alert` directly,
  rather than being smoothed into a relative diff.
* `vector/` — SQLite-backed `VectorStore` with brute-force cosine k-NN
  (`Neighbor` results carry `metadata`). Mismatched-dim rows are
  *skipped* rather than raised, so a namespace can be silently re-trained
  at a new embedding dim without poisoning new queries.
* `fingerprint/` — SHA-256 over predictions on a fixed canary input
  set, with deterministic rounding to 6 decimal places (silences CUDA
  non-determinism), `schema_version` and the precision baked into the
  digest. `compare_fingerprints` returns the v0.4 severity contract.

Permissions added to the v0.7 RBAC table:
`modality_check` (viewer), `vector_query` (viewer), `vector_add`
(writer), `fingerprint_compute` (writer).

`AppSettings.vector_store_path` (env `BIOMODEL_VECTOR_STORE_PATH`) opts
in to the vector store; without it the `/vector/*` endpoints return
`503`. The fingerprint and modality endpoints are stateless and always
available.

## Consequences

* The platform plugs into any OpenTelemetry collector with one env
  variable and **zero new mandatory dependencies** for users that don't
  want OTEL.
* `/modality/check` makes drift signals actionable per modality; an
  intensity shift on chest X-rays no longer competes for the same
  attention budget as a vocab drift on a discharge-notes classifier.
* The vector-store interface is deliberately a thin SQLite façade.
  When a deployment outgrows brute-force k-NN, swapping the
  storage/index for FAISS or hnswlib is a back-end change only — the
  `VectorStore` API and the `/vector/*` endpoints are stable.
* Fingerprinting closes the regulator's third question (*"is this still
  the model you signed off on?"*) without dragging in a hardware-rooted
  attestation requirement. A hardware TPM-rooted variant is on the
  roadmap but is **explicitly out of scope** for v0.11.
* We picked SHA-256 over a faster non-cryptographic hash because
  fingerprints are designed to be diffed against historical records on
  unsecured channels; collision-resistance dominates the ~10 µs cost.
