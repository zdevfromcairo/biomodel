# Forecasting & ETA-to-breach (v0.6)

> *A trend isn't an alert. A trend that will breach the alert threshold
> next Friday at the current trajectory is an alert.*

v0.6 adds a Holt's-linear forecaster on every metric the store remembers.
Given a metric series and a threshold, it returns:

- a point forecast for the next *H* steps,
- a confidence band (default 95% Gaussian on the in-sample residual stdev),
- an explicit **ETA-to-breach** in steps, and
- a severity (`ok` / `warn` / `alert`) so it can be wired into existing
  alerting like any other metric.

## Why Holt and not ARIMA?

Per-batch metric histories are short, irregularly-spaced and noisy. Holt's
linear method has two parameters, no order-selection step, no convergence
issues, and is trivially auditable by a model owner. That matters more than
a marginally better point forecast in a regulated context.

## Usage

```python
from biomodel_monitor.metrics.forecast import forecast_metric

result = forecast_metric(
    history=[0.05, 0.07, 0.09, 0.12, 0.15, 0.19],
    metric_name="psi_output_score",
    horizon=20,
    threshold=0.5,
    direction="above",
)

print(result.severity)         # "warn"
print(result.eta_to_breach)    # e.g. 14
for p in result.forecast[:3]:
    print(p.step, p.value, p.lower, p.upper)
```

## CLI

```bash
biomodel-monitor forecast \
  --store /var/lib/biomodel/biomodel.db \
  --model-id pathology-tumor-clf --model-version 1.0.0 \
  --metric psi_output_score --horizon 20 \
  --threshold 0.25 --direction above
```

## HTTP

```
GET /forecast?metric=psi_output_score
            &model_id=pathology-tumor-clf&model_version=1.0.0
            &horizon=20&threshold=0.25&direction=above
```

Response:

```json
{
  "metric": "psi_output_score",
  "severity": "warn",
  "eta_to_breach": 14,
  "threshold": 0.25,
  "direction": "above",
  "forecast": [
    {"step": 1, "value": 0.21, "lower": 0.17, "upper": 0.25},
    {"step": 2, "value": 0.22, "lower": 0.17, "upper": 0.27}
  ],
  "method": "holt"
}
```

## Severity rules

| State | Severity |
| ----- | -------- |
| Last observation already crosses the threshold | `alert` |
| Forecast crosses the threshold within `horizon` steps | `warn` |
| Otherwise | `ok` |

Use `direction="below"` for metrics where lower is worse (e.g. AUROC).

## Beyond Holt

The companion modules give you the rest of the toolkit:

- `metrics/conformal.py` — calibrate distribution-free prediction intervals
  and monitor empirical coverage.
- `metrics/concept_drift.py` — PCA reconstruction-error detector for
  multivariate input drift.
- `intelligence/causal.py` — interaction-effect attribution that singles
  out joint subgroups (e.g. `ScannerY × Stain=H&E`) the marginal version
  misses.
