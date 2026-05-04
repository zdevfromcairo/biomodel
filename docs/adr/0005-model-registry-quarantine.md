# 0005 — Model registry + quarantine as v0.9 governance primitive

* **Status:** Accepted (v0.9)

## Context

Through v0.8, BioModel Monitor could *detect* a problem (drift, miscalibration,
plausibility breach) and *notify* humans. It could not *act*: the operator had
to manually pull the model out of production via the application's own
deployment system.

For regulated medical AI we need a single source of truth for *"is this
model version cleared to serve traffic?"* and a tamper-evident record of
who changed that answer and when.

## Decision

Introduce ``biomodel_monitor.registry``: a SQLite-backed catalogue of
``(model_id, model_version)`` records with a `status` field
(`active` / `quarantined` / `retired`), plus a `lineage` table of directed
edges between versions.

Quarantine state changes are exposed as `POST /models/{id}/{ver}/quarantine`
and emit `model.quarantined` events; every mutation is appended to the
v0.7 hash-chained audit log when configured. The declarative
``policy/`` engine can issue a `quarantine` action when a rule matches.

Registry usage is **opt-in** (set `BIOMODEL_REGISTRY_PATH`). Without it,
the server retains the v0.8 behaviour. Without a registry, the v0.7
audit log still records *intent*, but no quarantine state is enforced.

## Consequences

* Operators get a one-call ("quarantine this version") workflow that is
  audit-trailed and event-streamed.
* Down-stream serving tooling can poll `GET /models?status=active` and
  refuse to route to anything else.
* The registry is intentionally tiny — it does *not* track training
  metrics, hyperparameters or weights. We defer that to MLflow / W&B and
  keep `training_data_hash` as the only mandatory link between the two
  systems.
* Per-call enforcement on `/runs` was deliberately *not* added in v0.9 —
  see the discussion in PR #N (we'd need to peek the batch before
  scheduling, which negates the point of `pipeline_runner`'s lazy I/O).
  Enforcement instead lives in the policy engine and at the deployment
  layer.
