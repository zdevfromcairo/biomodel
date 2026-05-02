# Reactive core (v0.8)

v0.8 reshapes BioModel Monitor's runtime around two ideas: *do the heavy
maths concurrently, and tell the operator about it the moment it happens.*

## Concurrent batch pipeline

The synchronous [`run_pipeline`](architecture.md) walks four heavy phases in
order — drift, calibration, subgroups, plausibility — then composes alerts.
For wide multimodal batches each phase takes hundreds of milliseconds and they
are independent.

[`run_pipeline_async`][async] schedules them on a `ThreadPoolExecutor` and
runs silent-failure (which depends on drift) afterwards:

```python
from biomodel_monitor.pipeline_async import run_pipeline_async

result, stats = run_pipeline_async(batch, baseline=baseline, workers=4)
print(stats.as_dict())
# {"drift_ms": 132, "calibration_ms": 41, "subgroups_ms": 88,
#  "plausibility_ms": 18, "silent_failure_ms": 12,
#  "total_ms": 162, "parallel": True, "workers": 4}
```

The result is **byte-identical** to the synchronous pipeline on the same
input — same alert keys, same metric rows, same persisted run id — so you can
cut over without changing dashboards.

CLI: `biomodel-monitor pipeline-async --config c.yaml --batch b.parquet --out reports/`.

[async]: https://github.com/zdevfromcairo/biomodel/blob/main/biomodel_monitor/pipeline_async.py

## Live event bus & WebSocket

Every notable event in the server is now broadcast through an in-process
pub/sub bus:

| Event type | Emitted from |
|---|---|
| `alert.emitted` | `POST /run` and the streaming `/ingest` flush |
| `run.completed` | end of `POST /run` |
| `incident.annotated` | `POST /alerts/{key}/annotations` |
| `baseline.promoted` | `POST /baselines/{id}/promote` |
| `ingest.flushed` | `POST /ingest` (size or age trigger) |
| `system.info` | welcome frame on websocket connect |

Subscribe over WebSocket:

```bash
websocat "ws://localhost:8080/ws/events?api_key=$BIOMODEL_API_KEY"
```

Or with the Python SDK:

```python
from biomodel_monitor.client import BioModelMonitorClient

c = BioModelMonitorClient("http://localhost:8080", api_key="…")
for evt in c.stream_events():
    if evt["type"] == "alert.emitted" and evt["payload"]["severity"] == "alert":
        page_oncall(evt)
```

A bounded history is kept so a client that connects *after* events fired can
catch up before subscribing live: `GET /events?limit=50&type=alert.emitted`.

Slow consumers can't block the publisher — when a subscriber's queue is full
the bus drops the *oldest* event for that subscriber and increments
`bus.dropped`.

## Embedding drift via MMD

Univariate PSI / KS only catches one feature at a time. Many real medical
drifts are *joint* — age × scanner × contrast shift together. The new
[`mmd_rbf`][mmd] computes the squared Maximum Mean Discrepancy between two
embedding sets with an RBF kernel and a permutation p-value:

```python
from biomodel_monitor.metrics.embedding_drift import mmd_rbf

res = mmd_rbf(reference_emb, current_emb, n_permutations=200)
print(res.value, res.p_value, res.severity)
# 0.062, 0.005, 'warn'
```

Bandwidth defaults to the median pairwise distance (median heuristic). The
estimator is unbiased (Gretton et al. 2012); the p-value uses the
Phipson–Smyth correction so it's never exactly zero.

Endpoint: `POST /mmd`. CLI: `biomodel-monitor mmd --reference ref.json --current cur.json`.

[mmd]: https://github.com/zdevfromcairo/biomodel/blob/main/biomodel_monitor/metrics/embedding_drift.py

## Online CUSUM change detection

Forecasting tells you *when* a metric will breach a threshold; **CUSUM** tells
you *the moment* a small persistent shift becomes statistically real. The
[`CUSUMMonitor`][cusum] is Page's two-sided CUSUM with reference value
`target` and slack `slack_k` (default 0.5σ — sensitive to a 1σ shift):

```python
from biomodel_monitor.metrics.cusum import CUSUMMonitor

mon = CUSUMMonitor(target=0.05, sigma=0.01, threshold=4.0)
for x in stream_of_ece_per_run():
    snap = mon.update(x)
    if snap.severity == "alert":
        notify(f"ECE drift detected at run {snap.detected_at} ({snap.direction})")
        mon.reset()
```

For one-shot use against a stored history:

```bash
biomodel-monitor cusum --input metric_series.json --threshold 4
# or, against the server:
curl -XPOST $URL/cusum -H "X-API-Key: $K" \
     -d '{"metric":"ece","model_id":"m","model_version":"1"}'
```

[cusum]: https://github.com/zdevfromcairo/biomodel/blob/main/biomodel_monitor/metrics/cusum.py

## Per-record local attribution

[`attribute_local`][local] computes leave-one-out influence per record, in
closed form for `score_mean` and `positive_rate`. Use it to answer
*"which actual records pulled this alert?"*:

```python
from biomodel_monitor.intelligence import attribute_local, aggregate_top_dimensions

la = attribute_local(batch.records, top_k=10)
print(la.top_positive[0].record_id, la.top_positive[0].contribution)

# Roll up to dimensions for a one-line summary.
aggregate_top_dimensions(la.top_positive + la.top_negative,
                        dimensions=("site_id", "scanner_id"))
```

Severity reflects how *concentrated* the contribution mass is — if a small
set of records explains most of the alert, the severity escalates.

[local]: https://github.com/zdevfromcairo/biomodel/blob/main/biomodel_monitor/intelligence/local_attribution.py

## Drift influence graph

`(dimension, value)` attribution alone misses *why* dimensions look correlated.
[`build_drift_graph`][graph] constructs a directed graph where each edge
`(d1=v1) → d2` carries the JS divergence between `d2`'s conditional and
pooled distributions. The CLI exports a Graphviz `dot`:

```bash
biomodel-monitor drift-graph --batch run42.parquet --out drift.dot
dot -Tsvg drift.dot > drift.svg
```

[graph]: https://github.com/zdevfromcairo/biomodel/blob/main/biomodel_monitor/intelligence/drift_graph.py

## OpenAPI as YAML

The server now also exposes its OpenAPI spec at `GET /openapi.yaml` for
codegen tooling that prefers YAML over JSON. The JSON form at
`/openapi.json` is unchanged.
