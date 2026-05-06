# Observability mesh & multi-modal (v0.11)

v0.11 makes BioModel Monitor a first-class citizen of every modern
observability stack and gives every modality the validators it actually
needs.

| Module | Purpose | Public surface |
| ------ | ------- | -------------- |
| `observability/` | Optional **OpenTelemetry** tracer + meter (no-op when the OTEL API package is missing) | `init_otel`, `span`, `counter`, `histogram`, `status`, `biomodel-monitor otel-status` |
| `modality/` | Per-modality drift validators that consume **lightweight summary stats** — no Pillow / NLTK dep | `check_image`, `check_text`, `check_tabular`, `check_modality`, `POST /modality/check`, `biomodel-monitor modality-check` |
| `vector/` | SQLite-backed embedding store with brute-force cosine **k-NN explanations** | `VectorStore`, `Neighbor`, `POST /vector/add`, `…/query`, `biomodel-monitor vector add|query` |
| `fingerprint/` | Deterministic SHA-256 over predictions on a fixed canary input set — detects silent weight substitution | `compute_fingerprint`, `compare_fingerprints`, `POST /fingerprint`, `…/compare`, `biomodel-monitor fingerprint` |

## 1. OpenTelemetry — opt-in, no-op fallback

```python
from biomodel_monitor import observability as obs

obs.init_otel()                # honours BIOMODEL_OTEL + standard OTEL_* env
with obs.span("drift_check", model_id="path-cls", batch_n=1024):
    run_drift_metric(...)

requests = obs.counter("monitor_requests_total")
requests.add(1, attributes={"endpoint": "/runs"})
```

If the `opentelemetry-api` package is **not** installed, every helper
turns into a free no-op. This is the same opt-in philosophy as v0.7
multi-tenancy and v0.9 governance: existing deployments are not asked
to take on a new mandatory dependency.

Runtime status:

```bash
biomodel-monitor otel-status
# {"enabled": true, "endpoint": "http://otel:4317", "service_name": "..."}
```

Configuration:

| Env var | Effect |
| ------- | ------ |
| `BIOMODEL_OTEL=1`            | Master switch. Defaults to off. |
| `OTEL_SERVICE_NAME`          | Defaults to `biomodel-monitor`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Standard OTEL endpoint. |

## 2. Per-modality validators

The v0.1–v0.9 drift detectors are modality-agnostic — they work on any
numeric column. Real medical AI deployments are **multi-modal**, and the
relevant signals differ:

* **Image** — resolution distribution, intensity stats, channel count.
  A grayscale → RGB substitution is silent in numeric drift but obvious
  here: `channels` change is treated as `severity=alert`.
* **Text** — token-length distribution, type/token ratio, vocab size,
  optional Jaccard overlap on the top tokens.
* **Tabular** — per-column missingness fraction; `worst_column` reported.

```python
from biomodel_monitor.modality import check_image

ref = {"width_mean": 1024, "height_mean": 768,
       "intensity_mean": 100, "intensity_std": 30, "channels": 3}
cur = {**ref, "channels": 1}
print(check_image(ref, cur).severity)  # "alert"
```

All results follow the v0.4 severity contract, so they wire into the
existing alert engine, the policy engine (v0.9) and the audit log.

## 3. Vector store + k-NN explanations

When a model is uncertain, the most useful explanation we can give a
clinician is often *"this case is similar to these labelled historical
cases"*.

```python
from biomodel_monitor.vector import VectorStore

vs = VectorStore("vec.db")
vs.add("ns", "case-001", embedding=[...], metadata={"label": "benign"})
vs.add("ns", "case-002", embedding=[...], metadata={"label": "malignant"})
result = vs.explain("ns", embedding=query_embedding, k=5)
```

The store is namespaced (`namespace`) so different models / cohorts
can't bleed into each other. Mismatched-dim rows are skipped rather than
raised — a namespace can be silently re-trained at a new embedding
size and the old vectors won't poison new queries.

The implementation is brute-force cosine (suitable for the sub-100k
vectors most monitoring deployments hold). The `VectorStore` class is a
clean seam: a future FAISS / hnswlib backend slots in without changing
callers.

## 4. Model fingerprinting

Quarantine + SBOM + audit log answer "*who*" and "*what*". Fingerprinting
answers the third question every regulator asks: *"how do you know the
model running in production today is still the model you signed off
on?"*

```python
from biomodel_monitor.fingerprint import (
    compare_fingerprints, compute_fingerprint,
)

fp = compute_fingerprint("canary-2025-q1", predictions)
print(fp.fingerprint)
# "sha256:6a8f…"  — deterministic to 6 decimal places (silences CUDA noise)

later = compute_fingerprint("canary-2025-q1", predictions_now)
print(compare_fingerprints(fp.fingerprint, later.fingerprint))
# {"matches": True, "severity": "ok"}  ← still the right model
```

The recipe:

1. Fix a small, immutable set of *canary inputs*. Identify the set by an
   opaque `canary_inputs_id` (typically its content hash).
2. Run the model under test on those inputs and capture the predictions.
3. Quantise to 6 decimal places (so harmless CUDA non-determinism doesn't
   fail the check), include `schema_version` + the precision in the
   payload, then SHA-256.

Two identical models always produce the same digest; any silent
substitution of weights, model architecture, or pre-processing flips it.

## Server endpoints

| Verb | Path | Permission |
| ---- | ---- | ---------- |
| `POST` | `/modality/check`         | `modality_check` (viewer) |
| `POST` | `/vector/add`             | `vector_add` (writer) |
| `POST` | `/vector/query`           | `vector_query` (viewer) |
| `POST` | `/fingerprint`            | `fingerprint_compute` (writer) |
| `POST` | `/fingerprint/compare`    | viewer |

`AppSettings.vector_store_path` (env `BIOMODEL_VECTOR_STORE_PATH`)
opts in to the vector store; without it the `/vector/*` endpoints return
`503`.

See ADR `0007-observability-mesh.md` for the design rationale.
