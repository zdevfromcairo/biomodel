# Closed loop (v0.10)

> *"The cheapest way to fix a model that just drifted is often to ask a human."*

v0.10 closes the loop between **detection** (v0.1–v0.8) and
**governance** (v0.9). When a metric flags drift or miscalibration,
operators now have four tightly-integrated tools to do something about
it without leaving the platform:

| Module | Purpose | Public surface |
| ------ | ------- | -------------- |
| `active_learning/` | Prioritise the records that are most informative for an expert to label | `score_record`, `bald_score`, `ActiveLearningQueue`, `POST /active-learning/enqueue`, `…/queue`, `…/{record}/label`, `…/stats`, `biomodel-monitor active-learning enqueue|queue|label` |
| `conformal/` | Wrap *any* black-box classifier in **distribution-free** prediction sets with marginal coverage `≥ 1 − α` | `calibrate`, `predict_sets`, `POST /conformal/calibrate`, `…/predict`, `biomodel-monitor conformal` |
| `feedback/` | Merge expert labels back in and re-run calibration on the human-validated subset | `merge_labels`, `recompute_ece` |
| `shadow/` | Compare a shadow-deployed canary against control on **the same records** with paired tests | `mcnemar`, `paired_bootstrap_diff`, `POST /shadow/mcnemar`, `…/bootstrap`, `biomodel-monitor shadow` |

## Why this matters

The v0.1–v0.8 detectors answer *"is the model still good?"*. v0.9 adds
*"what should we do about it?"* (quarantine, policy actions). v0.10
operationalises the **third** question every operator asks next:

* "Which 50 records should I send to a clinician *first*?" → active learning.
* "I don't trust the model's probabilities — can I still give downstream
  consumers a sound uncertainty?" → conformal prediction sets.
* "Once we have expert labels, has calibration actually moved?" → feedback.
* "Is this candidate model genuinely better than the one in production
  *on the records production saw*?" → shadow comparator (paired McNemar).

## 1. Active-learning queue

```python
from biomodel_monitor.active_learning import (
    ActiveLearningQueue, QueueItem, score_record,
)

q = ActiveLearningQueue("al.db")
for rid, probs in records:
    q.enqueue(QueueItem(
        model_id="path-cls", model_version="v3",
        record_id=rid,
        score=score_record(probs, strategy="entropy"),
        strategy="entropy",
    ))

# Reviewer pulls the top 25 highest-uncertainty records:
for item in q.next_batch("path-cls", "v3", limit=25):
    label = ask_clinician(item.record_id)         # your UI
    q.submit_label("path-cls", "v3", item.record_id, label=label)
```

Strategies:

* `entropy` — Shannon entropy of the prediction (default, robust).
* `margin` — `1 − (top1 − top2)`. Good for active learning near
  decision boundaries.
* `least_confidence` — `1 − max(p)`. Cheap to compute.
* **`bald`** — Bayesian Active Learning by Disagreement. Requires a list
  of MC-dropout posterior samples: it returns the **mutual information**
  between the label and the model parameters, which is exactly what you
  want when you can pay the inference cost for an MC ensemble.

The queue is plain SQLite with a lock (same pattern as the v0.9
registry); it is safe to share across FastAPI worker threads, and the
priority order survives a process restart.

## 2. Split-conformal prediction

Coverage guarantee:

> Under exchangeability of calibration + test data,
> `P( y_test ∈ predicted_set ) ≥ 1 − α`.

This holds **regardless** of whether the underlying model is itself
well-calibrated. It composes with the v0.4 calibration metrics: a model
can be over-confident *and* still produce sound conformal sets — that is
the whole point.

```python
from biomodel_monitor.conformal import calibrate, predict_sets

cal = calibrate(probs_calib, y_calib, alpha=0.1, score_fn="aps")
out = predict_sets(probs_test, cal)
print(out.value)   # mean prediction-set size
print(out.sets[0]) # e.g. [2, 5] — two plausible classes for record 0
```

Two scoring functions:

* **APS** (Adaptive Prediction Sets, Romano et al. 2020) — friendlier
  conditional coverage, slightly larger sets.
* **LAC** (`1 − p_y`, Sadinle et al. 2019) — tightest sets at the cost
  of conditional coverage.

The result honours the v0.4 severity contract: severity bands are placed
on **mean set size**, so a "the model has lost specificity"
deterioration shows up as a `warn`/`alert` exactly the way every other
metric does.

## 3. Expert-label feedback

```python
from biomodel_monitor.feedback import recompute_ece

res, batch = recompute_ece(
    record_ids, scores, prior_labels=[None]*len(scores),
    expert_items=q.labels("path-cls", "v3"),
)
print(batch.n_used, "expert labels used; ECE =", res.value, res.severity)
```

Merge rules are deliberately strict — expert always wins, unlabelled
records are skipped, and the function raises if nothing overlaps. This
prevents accidentally mixing synthetic labels into a calibration metric
that is supposed to reflect ground truth.

## 4. Shadow comparator

```python
from biomodel_monitor.shadow import mcnemar, paired_bootstrap_diff

# Per-record correctness flags from both models on the SAME inputs:
res = mcnemar(control_correct, canary_correct)
print(res.value, res.severity, res.extra["p_value"])

# Or any continuous loss (lower-is-better → flip the sign convention):
res2 = paired_bootstrap_diff(control_loss, canary_loss,
                             n_boot=2000, warn=0.02, alert=0.05)
```

Compared with the v0.9 mSPRT canary (which is *unpaired* and good for
streaming online comparisons), the paired tests in v0.10 give you much
more statistical power once you can hold the two models accountable on
identical records.

## Server endpoints

| Verb | Path | Permission |
| ---- | ---- | ---------- |
| `POST` | `/active-learning/enqueue` | `enqueue_label` (writer) |
| `GET`  | `/active-learning/{model_id}/{version}/queue` | viewer |
| `POST` | `/active-learning/{model_id}/{version}/{record_id}/label` | `submit_label` (operator) |
| `GET`  | `/active-learning/{model_id}/{version}/stats` | viewer |
| `POST` | `/conformal/calibrate` | `conformal_calibrate` (writer) |
| `POST` | `/conformal/predict`   | viewer |
| `POST` | `/shadow/mcnemar`     | `shadow_compare` (viewer) |
| `POST` | `/shadow/bootstrap`   | `shadow_compare` (viewer) |

All mutating endpoints emit hash-chained audit-log entries when
`AppSettings.audit_log_path` is configured. The active-learning queue
itself is opt-in via `BIOMODEL_ACTIVE_LEARNING_PATH`; without it the
endpoints return `503` so the rest of the API keeps working.

See ADR `0006-closed-loop.md` for the design rationale.
