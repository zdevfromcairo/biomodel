# 0006 — Closed-loop feedback as v0.10 capability

* **Status:** Accepted (v0.10)

## Context

By v0.9 the platform could **detect** a problem (drift, miscalibration,
plausibility breach), **explain** it (root-cause attribution, what-if),
**act** on it (quarantine via registry + policy engine), and produce a
**signed audit bundle**. What remained missing was a feedback loop:
once humans confirm or refute the alert, that information had nowhere to
go inside the platform.

In practice operators want four things, frequently and together:

1. A *prioritised list* of records to send to a clinician for labelling
   (active learning).
2. A way to wrap the model in **distribution-free** prediction sets, so
   downstream consumers see honest uncertainty even when the model itself
   is over-confident (conformal prediction).
3. A way to merge expert labels back in and **rerun** calibration on the
   human-validated subset — and only the human-validated subset.
4. A way to evaluate a candidate model in shadow against the production
   model on the **same records**, with paired statistics that have far
   more power than the unpaired v0.9 mSPRT canary.

## Decision

Add four loosely-coupled modules under
``biomodel_monitor/{active_learning,conformal,feedback,shadow}/``,
each shipping a small severity-contract result type, server endpoints
gated by new tenant permissions, and a CLI subcommand:

* `active_learning/` — `score_record`, `bald_score`, `ActiveLearningQueue`
  (SQLite, lock-protected, priority on `score DESC`).
* `conformal/` — `calibrate` and `predict_sets` with **APS** and **LAC**
  scoring functions and the standard `(n+1)(1-α)/n` finite-sample
  correction.
* `feedback/` — strict `merge_labels` (expert always wins, unlabelled
  records skipped, raises if no overlap) plus `recompute_ece`.
* `shadow/` — paired **McNemar** with an exact two-sided binomial p-value
  (no scipy) and a paired bootstrap CI on continuous metrics.

Permissions added to the v0.7 RBAC table:
`enqueue_label` (writer), `submit_label` (operator),
`shadow_compare` (viewer), `conformal_calibrate` (writer).

The active-learning queue itself is **opt-in** via
`BIOMODEL_ACTIVE_LEARNING_PATH`; without it the server returns `503` on
the queue endpoints. Every mutating endpoint emits a hash-chained audit
log entry when the v0.7 audit log is configured.

## Consequences

* The platform now closes the loop end-to-end: detection → triage →
  labelling → calibration recompute → shadow evaluation → governance
  action — all inside one auditable system.
* Conformal prediction composes orthogonally with the v0.4 calibration
  metrics. They measure different things: ECE measures whether the
  model's *probabilities* are honest; conformal coverage measures whether
  the *prediction set* is honest. Both can be `warn` or `alert`
  independently.
* The strict feedback merge prevents a footgun: silently mixing
  synthetic and human-validated labels into a calibration recompute would
  make the result un-interpretable.
* We deliberately did **not** add a built-in labelling UI in v0.10. The
  queue + API + audit log are sufficient for any in-house tool to drive
  the workflow; an opinionated UI lives in v0.11+.
* The shadow paired tests do not subsume the v0.9 mSPRT canary — mSPRT
  is the right tool for *online streaming* unpaired comparisons. Shadow
  is the right tool when you can replay the same records through both
  models.
