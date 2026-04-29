# Federated monitoring (v0.7)

A model deployed across many sites usually **cannot pool raw records** —
patient privacy, regulatory boundaries, or data sovereignty get in the
way. Each site, however, can publish *sufficient statistics* that are safe
to share. v0.7 adds an aggregator that takes a list of these per-site
summaries and computes the **pooled** metric *as if* the records had been
concatenated, plus a per-site contribution breakdown.

## What you can pool

| Metric kind | Per-site summary | Pooled output |
| ----------- | ---------------- | ------------- |
| Histogram drift (PSI / KL) | `HistogramSummary` (counts per aligned bin) | pooled distribution + per-site PSI vs. pool |
| Calibration (ECE) | `CalibrationSummary` (counts, positives, conf-sums per bin) | pooled ECE + per-site ECE |
| Continuous metric | `MomentSummary` (n, sum, sum-of-squares) | pooled mean / stdev + per-site z-score |

## Example

Each site computes its own summary and sends just that:

```python
from biomodel_monitor.federated import HistogramSummary, aggregate_histograms

site_a = HistogramSummary(site_id="hospA", bin_edges=[0, .25, .5, .75, 1],
                          counts=[120, 200, 180, 80])
site_b = HistogramSummary(site_id="hospB", bin_edges=[0, .25, .5, .75, 1],
                          counts=[40, 60, 70, 230])  # right-shifted

result = aggregate_histograms([site_a, site_b], warn_psi=0.10, alert_psi=0.25)
print(result.severity, result.per_site_psi)
```

Or over HTTP:

```bash
curl -X POST https://monitor.example.com/federate/drift \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d @summaries.json
```

```json
{
  "pooled_n": 980,
  "pooled_distribution": [0.16, 0.27, 0.25, 0.32],
  "per_site_psi": {"hospA": 0.07, "hospB": 0.41},
  "severity": "alert",
  "extra": {"warn_psi": 0.10, "alert_psi": 0.25, "worst_psi": 0.41}
}
```

The CLI does the same end-to-end:

```bash
biomodel-monitor federate --input summaries.json
```

## Privacy guarantees

The aggregator never receives, requests, or transmits a raw record. Every
input is a **count** or a **moment**. The number of decimal places in the
sum-of-squares is the only side channel — for medical data, that's in the
"informally safe" bucket; for stricter settings, layer a noise mechanism
(e.g. Gaussian DP) at the site before publishing the summary.

## Aligning bin edges

Histogram and calibration aggregation require *identical bin edges* across
sites. The maintainers' rule of thumb: agree on edges once, hard-code them
into the per-site summariser, and treat any change as a versioned schema
change. The aggregator refuses misaligned input rather than silently
re-binning, because silent re-binning is exactly how federated drift
analyses produce wrong answers.
