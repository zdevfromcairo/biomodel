# BioModel Monitor

**Post-deployment monitoring and assurance for multimodal medical AI.**

Generic ML monitoring tracks uptime and aggregate accuracy. Medical AI fails
differently — models can appear stable while becoming biologically implausible,
clinically miscalibrated, or distributionally brittle across sites, scanners,
protocols, and populations. BioModel Monitor is a specialized monitoring layer
that speaks the language of biomedical model risk.

This repository contains:

- **Phase 1** (`v0.1`) — offline batch monitor for pathology and
  multimodal-omics-adjacent models.
- **Phase 2** (`v0.2`) — operational monitoring: persistent metrics store,
  persistence-aware severity, incident workspace, threshold auto-tuning,
  rolling per-site baselines with explicit promotion, near-real-time
  ingestion, and pluggable notification channels.
- **Phase 3** (`v0.3`) — beyond pathology: radiology + multimodal-omics rule
  packs, multi-class calibration, fairness metrics, bootstrap CIs, cross-model
  dependency attribution, and signed regulatory export bundles.

## What it does

Given a batch of model predictions plus per-prediction metadata (site, scanner,
stain, tissue type, cohort, optional ground truth), BioModel Monitor:

1. **Validates** the schema and metadata.
2. **Computes drift** of inputs, outputs, and modality variables vs. a baseline.
3. **Measures calibration** (ECE, MCE, Brier) overall and per cohort.
4. **Slices subgroups** (site / scanner / stain / tissue / custom) with
   small-N-aware Wilson confidence intervals.
5. **Runs domain plausibility rules** (e.g. tumor probability vs. tissue area,
   marker co-expression, slide/tile consistency).
6. **Detects silent failure signatures** (entropy collapse, confidence/accuracy
   decoupling, prediction drift without input drift).
7. **Raises alerts** with severity, dedup, and root-cause hints.
8. **Renders reports** (Markdown + HTML) and powers an optional Streamlit
   dashboard.

The first version is built to answer one question for a model owner:

> *Is my model still behaving acceptably, and if not, exactly where is it
> breaking?*

## Install

```bash
pip install -e ".[dev,parquet,dashboard]"
```

## Quick start

```bash
# Run the bundled pathology example end-to-end
python -m examples.pathology_pipeline.run_example

# Or use the CLI with a YAML config
biomodel-monitor run --config examples/pathology_pipeline/config.yaml
```

The run produces an HTML and Markdown report under `reports_out/` plus a
JSON metrics bundle.

## Operational monitoring (v0.2)

```bash
# Persist runs, alerts and metrics to a SQLite store by adding `store: <path>` to
# your config; persistence-aware severity is then enabled automatically.

# Watch a directory for new batches and feed them to a queue:
biomodel-monitor watch         --config cfg.yaml --directory ./incoming --queue ./q.json
biomodel-monitor process-queue --config cfg.yaml --queue ./q.json

# Triage:
biomodel-monitor incidents list     --store mon.db --model-id m1 --model-version 1.0.0
biomodel-monitor incidents annotate --store mon.db --model-id m1 --model-version 1.0.0 \
    --key <alert-key> --kind label --label fp

# Learn baselines and promote them:
biomodel-monitor baseline-update  --store mon.db --input new_batch.csv --cohort lung
biomodel-monitor baseline-promote --store mon.db --model-id m1 --model-version 1.0.0 --cohort lung

# Tune thresholds from labeled history:
biomodel-monitor tune-thresholds  --store mon.db --model-id m1 --model-version 1.0.0
```

## Regulatory export (v0.3)

```bash
biomodel-monitor export-bundle \
    --store mon.db --report-dir reports_out --out-dir exports/ \
    --model-id m1 --model-version 1.0.0 --batch-id batch_2026_04_15
biomodel-monitor verify-bundle exports/batch_2026_04_15__20260415T120000Z
```

## Layout

```
biomodel_monitor/
  schema/         pydantic models for predictions, metadata, cohort
  ingest/         CSV / Parquet / JSONL loaders + validation
  metrics/        drift, calibration (binary + multiclass), subgroup,
                  plausibility (pathology + radiology + omics), silent_failure,
                  fairness, bootstrap CIs
  baselines/      reference-window storage + rolling learner with promotion
  alerts/         threshold engine, severity, dedup, threshold auto-tuning
  store/          persistent SQLite metrics + incident store
  incidents/      incident workspace (ack / resolve / comment / label)
  scheduler/      directory watcher + filesystem queue (near-real-time)
  notifications/  pluggable channels (file, webhook, email)
  dependency/     cross-model dependency graph + alert attribution
  reports/        Jinja2 HTML + Markdown templates + regulatory export pack
  dashboard/      Streamlit dashboard
  cli.py          `biomodel-monitor run|watch|process-queue|incidents|...`
examples/
  pathology_pipeline/   runnable synthetic pathology integration
docs/             product spec, metric library, roadmap, etc.
tests/            unit / integration / domain
```

## Documentation

- [Product spec](docs/product_spec.md)
- [Metric library](docs/metric_library.md)
- [Competition analysis](docs/competition_analysis.md)
- [Pilot memo](docs/pilot_memo.md)
- [Security & privacy checklist](docs/security_privacy_checklist.md)
- [Roadmap](docs/roadmap.md)

## License

MIT — see [LICENSE](LICENSE).
