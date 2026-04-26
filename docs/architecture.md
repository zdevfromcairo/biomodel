# Architecture

BioModel Monitor is a layered system. Each layer has one job and a stable
contract with the next; you can swap any layer (e.g. Postgres for SQLite, a
new notification channel, a new metric) without touching the rest.

```mermaid
flowchart TB
    subgraph Ingest
      direction LR
      L[Loader<br/>CSV / Parquet / JSONL] --> V[Schema validator]
    end

    subgraph Pipeline
      direction LR
      M1[Drift] & M2[Calibration] & M3[Subgroup] & M4[Plausibility] & M5[Silent failure] & M6[Fairness]
      M1 & M2 & M3 & M4 & M5 & M6 --> AE[Alert engine]
    end

    subgraph Persistence
      MS[(Metrics store)]
      BS[(Baseline store)]
      IS[(Incidents)]
    end

    subgraph Delivery
      RP[Reports<br/>HTML · MD · JSON]
      EX[Regulatory bundle]
      NCh[Notifications]
      DSH[Streamlit dashboard]
      SRV[FastAPI server]
    end

    subgraph Intelligence ["Intelligence (v0.5)"]
      AT[Attribution]
      CP[Changepoint]
      WIF[What-if]
      AL[Active learning]
      MC[Model card]
    end

    Ingest --> Pipeline --> Persistence
    Persistence --> Delivery
    Persistence --> Intelligence
    AE --> NCh
```

## Layers

### 1. Schema (`biomodel_monitor/schema/`)
Pydantic v2 models for `PredictionRecord`, `BatchMetadata`, `PredictionBatch`,
plus a small declarative contract format for required fields and registered
sites.

### 2. Ingest (`ingest/`)
Loaders for CSV, Parquet, and JSONL. The loader is the only place that touches
your raw data.

### 3. Metrics (`metrics/`)
Each metric returns a dataclass with `.severity ∈ {ok, warn, alert}` and
`.as_dict()`. This is the single contract everything downstream consumes.
Implementations are pure-Python (numpy/pandas only) so they're easy to audit.

### 4. Baselines (`baselines/`)
Reference distributions for drift and calibration comparisons. The
`RollingBaselineLearner` folds new batches into a candidate baseline; an
operator explicitly **promotes** a candidate to live.

### 5. Alerts (`alerts/`)
Turns metric severities into deduplicated, persistence-aware alerts. The
`threshold_tuner` proposes new per-category thresholds from labeled history.

### 6. Store (`store/`)
A `Storage` Protocol with two concrete adapters:

- `MetricsStore` — SQLite (default, zero-ops).
- `PostgresStore` — production backend (via `psycopg`).

Persists batches, runs, alerts, metrics, baselines, annotations.

### 7. Incidents (`incidents/`)
Workspace for ack / resolve / comment / label on alerts. Annotations feed
threshold tuning and active-learning ranking.

### 8. Notifications (`notifications/`)
Pluggable channels: file, webhook (signed), Slack, PagerDuty, Microsoft Teams.
All built on a common `WebhookChannel` base with HMAC-SHA256 signing and
exponential-backoff retry.

### 9. Reports (`reports/`)
Jinja2 HTML + Markdown templates plus the regulatory export packer (signed
manifest with SHA-256 hashes).

### 10. Server (`server/`) &nbsp;<small>v0.4</small>
FastAPI app exposing the store and pipeline over REST, with API-key auth,
structured JSON access logs, and a Prometheus `/metrics` endpoint.

### 11. Intelligence (`intelligence/`) &nbsp;<small>v0.5</small>
- **`attribution`** — for any alert, rank which dimension/value contributed most.
- **`changepoint`** — divisive segmentation on metric history; pinpoints **when** drift began.
- **`whatif`** — recompute drift after counterfactually excluding a cohort/site/scanner.
- **`anomaly`** — robust z-score (MAD) layered onto every metric.
- **`active_learning`** — rank open incidents by expected information gain.
- **`modelcard`** — auto-generate a Markdown model card from the store.

### 12. Dashboard (`dashboard/`)
Streamlit app for triage; reads the same store the server writes to.

## Data flow for a single batch

```mermaid
sequenceDiagram
    autonumber
    participant U as Operator
    participant L as Loader
    participant P as Pipeline
    participant M as Store
    participant A as Alert engine
    participant N as Notifications

    U->>L: batch.csv
    L->>P: PredictionBatch
    P->>P: drift / calibration / subgroup / plausibility ...
    P->>A: metric severities
    A->>M: persist run + alerts
    A->>N: dispatch (severity ≥ warn)
    P->>U: HTML / MD / JSON report
```
