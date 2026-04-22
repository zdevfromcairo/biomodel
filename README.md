# BioModel Monitor

**Post-deployment monitoring and assurance for multimodal medical AI.**

Generic ML monitoring tracks uptime and aggregate accuracy. Medical AI fails
differently — models can appear stable while becoming biologically implausible,
clinically miscalibrated, or distributionally brittle across sites, scanners,
protocols, and populations. BioModel Monitor is a specialized monitoring layer
that speaks the language of biomedical model risk.

This repository contains **Phase 1**: an offline batch monitor for pathology
and multimodal-omics-adjacent models.

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

## Layout

```
biomodel_monitor/
  schema/         pydantic models for predictions, metadata, cohort
  ingest/         CSV / Parquet / JSONL loaders + validation
  metrics/        drift, calibration, subgroup, plausibility, silent_failure
  baselines/      reference-window storage
  alerts/         threshold engine, severity, dedup
  reports/        Jinja2 HTML + Markdown templates
  dashboard/      Streamlit dashboard
  cli.py          `biomodel-monitor run --config <yaml>`
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
