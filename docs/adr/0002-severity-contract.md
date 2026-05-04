# 0002 — Single severity contract for all metric results

* **Status:** Accepted (v0.1, ratified retroactively v0.9)

## Context

The pipeline composes results from heterogeneous detectors — drift,
calibration, subgroup, plausibility, silent-failure, MMD, CUSUM,
Wasserstein. Each detector ships its own dataclass, but downstream
machinery (alert engine, persistence, dashboard, regulatory bundle) needs
to treat them uniformly.

## Decision

Every metric result dataclass exposes:

1. ``.severity`` ∈ ``{"ok", "warn", "alert"}``.
2. ``.as_dict()`` returning a plain JSON-serialisable dict with stable
   keys.
3. A ``name`` field that is unique within the metric family.

The ``AlertEngine`` consumes only this contract. New metrics that violate it
are rejected by the alerts unit tests.

## Consequences

* Adding a new metric is a single file plus tests — no need to touch
  pipeline / store / dashboard.
* The wire format is implicitly stable: any consumer that knows the
  contract handles any future metric.
* We give up some expressivity (e.g. fine-grained severity levels). We
  judge the simplicity worth it; finer signals live in `extra`.
