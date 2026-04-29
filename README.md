<h1 align="center">BioModel Monitor</h1>

<p align="center">
  <strong>Post-deployment monitoring & assurance for multimodal medical AI.</strong><br/>
  Drift, calibration, fairness, plausibility, silent-failure, and regulatory bundles —
  with a service, an HTTP API, and an intelligence layer that <em>explains</em> the alerts.
</p>

<p align="center">
  <a href="https://github.com/zdevfromcairo/biomodel/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/zdevfromcairo/biomodel/ci.yml?branch=main&label=CI"/></a>
  <a href="https://github.com/zdevfromcairo/biomodel/actions/workflows/docs.yml"><img alt="docs" src="https://img.shields.io/github/actions/workflow/status/zdevfromcairo/biomodel/docs.yml?branch=main&label=docs"/></a>
  <a href="https://zdevfromcairo.github.io/biomodel/"><img alt="site" src="https://img.shields.io/badge/site-mkdocs--material-009485"/></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776ab"/>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-blue"/>
  <img alt="version" src="https://img.shields.io/badge/version-0.5.0-success"/>
  <img alt="tests" src="https://img.shields.io/badge/tests-145%20passing-brightgreen"/>
</p>

<p align="center">
  <a href="https://zdevfromcairo.github.io/biomodel/quickstart/">Quickstart</a> ·
  <a href="https://zdevfromcairo.github.io/biomodel/architecture/">Architecture</a> ·
  <a href="https://zdevfromcairo.github.io/biomodel/server/">Server</a> ·
  <a href="https://zdevfromcairo.github.io/biomodel/intelligence/">Intelligence</a> ·
  <a href="https://zdevfromcairo.github.io/biomodel/metric_library/">Metrics</a>
</p>

---

## Why

Generic ML monitoring tracks uptime and aggregate accuracy. **Medical AI fails differently.**
Models can appear stable on a dashboard while becoming biologically implausible, clinically
miscalibrated, or distributionally brittle across sites, scanners, protocols, and populations.

BioModel Monitor is a specialized monitoring layer that speaks the language of biomedical
model risk and answers the only question a model owner cares about:

> *Is my model still behaving acceptably, and if not, exactly **where** is it breaking,
> **why**, and **what** would fix it?*

## Highlights

- **Domain-aware metrics** — drift, calibration (binary + multiclass), fairness, subgroup
  with Wilson CIs, plausibility rule packs (pathology, radiology, omics), silent-failure
  signatures, bootstrap CIs.
- **Service, not a script** *(v0.4)* — FastAPI HTTP server, OpenAPI, API-key auth,
  structured JSON logs, Prometheus `/metrics`, signed webhooks, **Slack / PagerDuty /
  Microsoft Teams**, **SQLite or Postgres**, Docker Compose.
- **Explains itself** *(v0.5)* — per-alert root-cause attribution, changepoint detection,
  counterfactual *what-if* drift, robust-z anomaly score, active-learning incident queue,
  auto-generated model cards.
- **Streams + forecasts + has an SDK** *(v0.6)* — server-side micro-batching `/ingest`
  endpoint, Holt's-linear **drift forecasting with ETA-to-breach**, split-conformal
  prediction intervals, PCA concept-drift detector, interaction-effect attribution,
  zero-dependency **Python SDK**.
- **Audit-ready** — signed regulatory export bundles (SHA-256 manifest), persistent
  annotations, persistence-aware severity, per-cohort fairness summary.

## Architecture

```mermaid
flowchart LR
    subgraph Sources
      B[Batch CSV / Parquet / JSONL]
      S[Streaming source]
    end
    B --> W[Watcher]
    S --> W
    W --> Q[(Queue)]
    Q --> P[Pipeline]
    P --> M[(Metrics store<br/>SQLite / Postgres)]
    P --> A[Alert engine]
    A --> N{{Notifications<br/>Slack · PagerDuty · Teams · Webhook}}
    M --> API[FastAPI server]
    M --> D[Streamlit dashboard]
    M --> R[Reports + signed bundle]
    M --> I[Intelligence<br/>attribution · changepoint · whatif · model card]
    API --> EXT[External tooling]
```

## Quickstart

```bash
# Install (with all optional extras)
pip install -e ".[dev,parquet,dashboard,server]"

# Run the bundled pathology example end-to-end
python -m examples.pathology_pipeline.run_example

# Or via the CLI with a YAML config
biomodel-monitor run --config examples/pathology_pipeline/config.yaml
```

The run produces an HTML + Markdown report under `reports_out/` plus a JSON metrics bundle.

### As a service (v0.4)

```bash
biomodel-monitor serve --store ./biomodel.db --api-key dev-key --port 8080

# OpenAPI:    http://localhost:8080/docs
# Health:     http://localhost:8080/health
# Prometheus: http://localhost:8080/metrics
```

### Explain an alert (v0.5)

```bash
# When did PSI start drifting? (changepoints + robust-z anomaly score)
biomodel-monitor explain     --store mon.db --model-id m1 --model-version 1.0.0 --metric psi

# What if we exclude ScannerY?
biomodel-monitor whatif      --input new.csv --baseline baseline.json --exclude scanner_id=ScannerY

# Auto-generated model card from the persistent store
biomodel-monitor model-card  --store mon.db --model-id m1 --model-version 1.0.0 --out card.md
```

### The full stack with Docker Compose

```bash
docker compose up --build
```

| Service     | Port | Purpose                                       |
| ----------- | ---- | --------------------------------------------- |
| `server`    | 8080 | FastAPI HTTP API                              |
| `watcher`   |  —   | Watches `./incoming/` for new batches         |
| `dashboard` | 8501 | Streamlit triage dashboard                    |

All three share the same SQLite store on a named volume; switch to Postgres
by setting `BIOMODEL_STORE_DSN` on the `server` service.

## What's new

- **v0.6.0 — Streaming, Forecasting & SDK.** Server-side micro-batching `/ingest`,
  Holt's-linear drift forecasting with ETA-to-breach, split-conformal intervals,
  PCA concept-drift detector, interaction-effect attribution, zero-dependency Python SDK.
- **v0.5.0 — Intelligence & Explanation.** Root-cause attribution, changepoint detection,
  counterfactual *what-if* drift, anomaly score, active-learning queue, auto model cards.
- **v0.4.0 — Server & Stack.** FastAPI server, Storage Protocol with SQLite + Postgres,
  Prometheus metrics, signed webhooks, Slack / PagerDuty / Teams, Docker Compose.
- **v0.3.0 — Beyond pathology.** Multiclass calibration, fairness, bootstrap CIs,
  radiology + omics rule packs, cross-model dependency graph, signed regulatory bundle.
- **v0.2.0 — Operational.** Persistent SQLite store, persistence-aware severity, incident
  workspace, threshold auto-tuning, rolling baselines with promotion, near-real-time
  ingestion, pluggable notifications.
- **v0.1.0 — Offline batch monitor.** Drift, calibration, subgroup, plausibility,
  silent failure, alerts, HTML/MD reports, Streamlit dashboard.

Full history: [CHANGELOG.md](CHANGELOG.md).

## Layout

```
biomodel_monitor/
  schema/         Pydantic models for predictions, batch metadata, cohort
  ingest/         CSV / Parquet / JSONL loaders + contract validation
  metrics/        drift, calibration (binary + multiclass), subgroup,
                  plausibility (pathology + radiology + omics), silent_failure,
                  fairness, bootstrap CIs
  baselines/      reference-window storage + rolling learner with promotion
  alerts/         threshold engine, severity, dedup, threshold auto-tuning
  store/          Storage Protocol; SQLite + Postgres adapters         (v0.4)
  incidents/      incident workspace (ack / resolve / comment / label)
  scheduler/      directory watcher + filesystem queue
  notifications/  webhook (signed), Slack, PagerDuty, Teams, file       (v0.4)
  intelligence/   attribution, changepoint, whatif, anomaly,
                  active learning, model card,                           (v0.5)
                  causal interaction-effect attribution                  (v0.6)
  metrics/        + forecast (Holt's), conformal, concept_drift          (v0.6)
  streaming/      WindowBuffer micro-batching for /ingest                (v0.6)
  client/         Python SDK (stdlib-only)                               (v0.6)
  dependency/     cross-model dependency graph + alert attribution
  reports/        Jinja2 HTML + Markdown templates + regulatory bundle
  server/         FastAPI app, API-key auth, /metrics, structured logs,  (v0.4)
                  /ingest streaming, /forecast                           (v0.6)
  dashboard/      Streamlit dashboard
  cli.py          biomodel-monitor run|watch|process-queue|incidents|
                  serve|explain|whatif|model-card|forecast|ingest|       (v0.6)
                  client-call
examples/
  pathology_pipeline/   runnable synthetic pathology integration
docs/              MkDocs Material site (deployed to GitHub Pages)
tests/             unit / integration / domain   (176 tests)
```

## Documentation

The full docs are built with **MkDocs Material** and deployed to GitHub Pages on every
push to `main` — see [`zdevfromcairo.github.io/biomodel`](https://zdevfromcairo.github.io/biomodel/).

Highlights:

- [Quickstart](docs/quickstart.md)
- [Architecture](docs/architecture.md)
- [The HTTP server (v0.4)](docs/server.md)
- [Intelligence layer (v0.5)](docs/intelligence.md)
- [Streaming ingestion (v0.6)](docs/streaming.md)
- [Forecasting & ETA-to-breach (v0.6)](docs/forecasting.md)
- [Python SDK (v0.6)](docs/sdk.md)
- [Tutorial — your first run](docs/tutorial.md)
- [Operating in production](docs/operations.md)
- [Notifications](docs/notifications.md)
- [Regulatory export](docs/regulatory.md)
- [Metric library](docs/metric_library.md)
- [Security & privacy checklist](docs/security_privacy_checklist.md)
- [Roadmap](docs/roadmap.md)

## Develop

```bash
pip install -e ".[dev,server,parquet,dashboard,docs]"
ruff check biomodel_monitor tests
python -m pytest -q
mkdocs serve   # local docs preview at http://127.0.0.1:8000
```

## License

MIT — see [LICENSE](LICENSE).
