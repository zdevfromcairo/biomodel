# The HTTP server (v0.4)

`biomodel-monitor serve` starts a FastAPI app that exposes the metrics store
and pipeline over REST, so external systems (orchestrators, dashboards, on-call
runbooks) can drive the monitor instead of running CLIs.

## Start it

```bash
biomodel-monitor serve \
    --store ./biomodel.db \
    --api-key $BIOMODEL_API_KEY \
    --host 0.0.0.0 --port 8080
```

For development you can pass `--no-auth`. Multiple `--api-key` flags are
allowed (e.g. one per consuming service).

Configure via environment instead of flags:

```bash
export BIOMODEL_STORE_PATH=/data/biomodel.db
export BIOMODEL_API_KEYS=alice-key,bob-key
export BIOMODEL_CORS=https://app.example.com
export BIOMODEL_LOG_LEVEL=INFO
biomodel-monitor serve --host 0.0.0.0
```

## Endpoints

| Method | Path                                | Purpose                                                   |
| ------ | ----------------------------------- | --------------------------------------------------------- |
| GET    | `/health`                           | Liveness (no auth)                                        |
| GET    | `/metrics`                          | Prometheus exposition (no auth)                           |
| GET    | `/docs`                             | OpenAPI / Swagger UI                                      |
| GET    | `/runs`                             | List recent pipeline runs                                 |
| POST   | `/runs`                             | Trigger a pipeline run on a batch path                    |
| GET    | `/alerts`                           | List alerts (filter by run / model / version / key)       |
| POST   | `/alerts/{key}/annotations`         | ack / resolve / comment / label                           |
| GET    | `/incidents`                        | Open incidents, ranked by severity × persistence          |
| GET    | `/baselines`                        | List baselines (candidate / promoted)                     |
| POST   | `/baselines/{id}/promote`           | Promote a candidate baseline                              |
| GET    | `/changepoints/{metric}`            | Changepoint + anomaly analysis on a metric's history      |
| POST   | `/whatif`                           | Counterfactual drift recomputation                        |
| GET    | `/model-card`                       | Auto-generated Markdown model card                        |

## Auth

Every authenticated endpoint requires `X-API-Key: <key>` (matches one of
`--api-key`). Auth failures are logged with the (truncated) key id so you can
correlate misuse.

## Observability

Every request emits a structured JSON log line:

```json
{"ts":1714056300123,"level":"INFO","msg":"request","method":"GET","path":"/alerts","status":200,"duration_ms":4}
```

The `/metrics` endpoint exposes:

- `biomodel_http_requests_total{path, method, status}` — counter
- `biomodel_http_request_duration_seconds{path}` — histogram
- `biomodel_alerts_emitted_total{severity}` — counter

These are emitted in plain Prometheus text format with **no** `prometheus_client`
dependency.

## Storage backends

The server talks to a `Storage` Protocol. Two implementations ship today:

- **SQLite** (`MetricsStore`) — default, file-based, zero-ops. Great for a
  single-tenant deployment.
- **Postgres** (`PostgresStore`) — for multi-tenant production. Uses the
  same DDL ported to Postgres; enable with the `postgres` extra:

  ```bash
  pip install ".[postgres]"
  export BIOMODEL_STORE_DSN=postgresql://user:pass@db/biomodel
  ```

Both adapters implement the same `Storage` Protocol so the rest of the system
is agnostic.
