# Quickstart

Run the bundled pathology example end-to-end in under a minute.

## 1. Install

```bash
pip install -e ".[dev,parquet,dashboard,server]"
```

See [Install](install.md) for optional extras (`postgres`, `docs`, etc.).

## 2. Run the bundled example

```bash
python -m examples.pathology_pipeline.run_example
```

This produces an HTML and Markdown report under
`examples/pathology_pipeline/out/reports/`, plus a JSON metrics bundle.

## 3. Run from a config

```bash
biomodel-monitor run --config examples/pathology_pipeline/config.yaml
```

## 4. Start the HTTP server (v0.4)

```bash
biomodel-monitor serve \
    --store ./biomodel.db \
    --api-key dev-key \
    --host 0.0.0.0 --port 8080
```

Then:

```bash
curl -H 'X-API-Key: dev-key' http://localhost:8080/health
curl -H 'X-API-Key: dev-key' http://localhost:8080/metrics   # Prometheus
```

OpenAPI is served at `http://localhost:8080/docs`.

## 5. Explore an alert (v0.5)

```bash
# Time-series changepoint + anomaly score for a metric
biomodel-monitor explain \
    --store ./biomodel.db \
    --model-id m1 --model-version 1.0.0 \
    --metric psi

# Counterfactual: "what would drift look like without ScannerY?"
biomodel-monitor whatif \
    --input new_batch.csv \
    --baseline baseline.json \
    --exclude scanner_id=ScannerY

# Auto-generated model card from the persistent store
biomodel-monitor model-card \
    --store ./biomodel.db \
    --model-id m1 --model-version 1.0.0 \
    --out model_card.md
```

## 6. Spin up the full stack with Docker Compose

```bash
docker compose up --build
```

That starts:

| Service     | Port | Purpose                                       |
| ----------- | ---- | --------------------------------------------- |
| `server`    | 8080 | FastAPI HTTP API (`/health`, `/metrics`, ...) |
| `watcher`   |  —   | Watches `./incoming/` for new batches         |
| `dashboard` | 8501 | Streamlit triage dashboard                    |

All three share a SQLite store on a named volume.
