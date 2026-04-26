# Intelligence layer (v0.5)

Most monitoring tools tell you *that* something broke. v0.5 is about telling
you *why*, *when*, and *what would fix it*.

## Root-cause attribution

For any alert, attribute the signal to the dimension/value pair that
contributed the most to the deviation:

```python
from biomodel_monitor.intelligence import attribute_alert

attr = attribute_alert("output_drift", batch.records, top_k=5)
for c in attr.contributors:
    print(c.dimension, c.value, c.contribution, c.delta, c.share)
```

The score is `|delta_from_pooled_mean| × share_of_records`, weighted by the
group size with a small-N guard (`min_n=20` by default). It works on
`site_id`, `scanner_id`, `stain`, `tissue_type`, `cohort`, plus any features
present in the records.

## Changepoint detection

Given a metric's history, locate the points in time where its mean shifted:

```bash
biomodel-monitor explain \
    --store mon.db \
    --model-id m1 --model-version 1.0.0 \
    --metric psi
```

The detector is a divisive segmentation of the series using a
mean-shift score (a CUSUM-style statistic). It returns indices, segment
means, and per-cut scores. You get a clear picture of *when* drift began,
not just that it's there now.

## Counterfactual drift ("what-if")

Recompute drift on a batch after dropping records that match a filter:

```bash
biomodel-monitor whatif \
    --input new_batch.csv \
    --baseline baseline.json \
    --exclude scanner_id=ScannerY \
    --exclude site_id=SiteB
```

The output reports drift **before** vs. **after** the exclusion. This is the
fastest way to confirm a hypothesis like *"this is all coming from ScannerY"*.

## Anomaly score

A robust z-score (median / MAD) layered onto every metric history:

```python
from biomodel_monitor.intelligence import robust_zscore
result = robust_zscore(history)
result.score, result.severity
```

`severity = "alert"` when `|z| > 5.0`, `"warn"` when `|z| > 3.0`. MAD-based
so it's not blown up by the very outliers it's trying to flag.

## Active-learning incident queue

Rank open incidents by expected information-gain — the labeling work that
will most improve future threshold tuning:

```python
from biomodel_monitor.intelligence import rank_incidents_for_review
proposals = rank_incidents_for_review(
    store, model_id="m1", model_version="1.0.0", top_k=10,
)
```

Incidents with `0 < tp_rate < 1` for the category (i.e. ambiguous categories)
score highest.

## Auto model card

Generate a publishable Markdown model card from what's already in the store —
intended use, training data, monitoring window, observed alert taxonomy,
calibration headline, fairness summary, residual risks:

```bash
biomodel-monitor model-card \
    --store mon.db \
    --model-id m1 --model-version 1.0.0 \
    --out model_card.md
```
