# 0003 — SQLite as the default persistence backend

* **Status:** Accepted (v0.2)

## Context

Monitoring tools are often deployed in three very different environments:

1. A radiologist's laptop running the dashboard against a folder of
   parquet files.
2. A single VM in a hospital DMZ serving one model.
3. A Kubernetes cluster with multiple writers and a managed database.

The first two environments dominate early adoption. Both want zero
operational burden — no separate database to install, configure, or back up.

## Decision

Default ``MetricsStore`` is SQLite. A ``StorageBackend`` abstraction lets
operators swap in Postgres without changing pipeline code; the Helm chart
exposes both DSNs.

## Consequences

* `pip install` plus `biomodel-monitor run` produces a complete, persistent
  installation in seconds. This is critical for pilot uptake.
* Cross-process writers must coordinate. We ship `check_same_thread=False`
  + locks where needed (e.g. v0.9 `ModelRegistry`); for high-write fan-out
  operators must move to Postgres.
* SQLite cannot be stretched to multi-region active/active. We accept
  that — operators with that need are exactly the population that wants
  Postgres.
