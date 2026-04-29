# Python SDK (v0.6)

`biomodel_monitor.client` is a stdlib-only Python SDK for the BioModel
Monitor HTTP API. **Zero extra dependencies** — installing the core package
is enough, even on locked-down clinical terminals.

## Quickstart

```python
from biomodel_monitor.client import BioModelMonitorClient

c = BioModelMonitorClient(
    "https://monitor.example.com",
    api_key="…",          # omit if the server is in --no-auth mode
    timeout=30.0,
)

c.health()
c.list_runs(model_id="pathology-tumor-clf", limit=10)
c.list_alerts(model_id="pathology-tumor-clf", model_version="1.0.0")
c.annotate("OUTPUT_DRIFT", kind="ack", actor="alice@hospital.org",
           comment="known scanner cutover, monitoring")
c.changepoints("psi_output_score",
               model_id="pathology-tumor-clf", model_version="1.0.0")
c.forecast(model_id="pathology-tumor-clf", model_version="1.0.0",
           metric="psi_output_score", horizon=20, threshold=0.25)
c.model_card(model_id="pathology-tumor-clf", model_version="1.0.0")
```

## Streaming records from your inference server

```python
records = []
def on_prediction(rec_dict):
    records.append(rec_dict)
    if len(records) >= 64:
        c.ingest("pathology-tumor-clf", "1.0.0", records)
        records.clear()
```

## Error handling

Non-2xx responses raise `biomodel_monitor.client.HTTPError`:

```python
from biomodel_monitor.client import HTTPError

try:
    c.health()
except HTTPError as e:
    print(e.status, e.body)
```

## Testable transport

The SDK accepts a custom transport, so unit tests don't need a network or a
mock server:

```python
def fake(method, url, headers, body):
    return 200, b'{"status": "ok", "version": "0.6.0"}'

c = BioModelMonitorClient("https://m", api_key="x", transport=fake)
c.health()  # {'status': 'ok', 'version': '0.6.0'}
```

## CLI escape hatch

```bash
biomodel-monitor client-call \
  --server https://monitor.example.com --api-key $KEY \
  --method GET /alerts?model_id=pathology-tumor-clf
```
