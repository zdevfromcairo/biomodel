# Tutorial — your first monitored model

This walks you through the *minimum* path: install, prepare a tiny batch,
run the monitor, inspect the report, then promote the result into the
persistent store.

## 1. Install

```bash
pip install -e ".[dev,parquet,dashboard,server]"
```

## 2. Inspect a batch

The bundled pathology example contains a synthetic 600-record batch:

```bash
head -3 examples/pathology_pipeline/data/current_2026_04_15.csv
```

Each row is a `PredictionRecord`: a model id, a score, a site / scanner /
stain / tissue, optional ground truth, optional features.

## 3. Run

```bash
biomodel-monitor run --config examples/pathology_pipeline/config.yaml
```

You should see something like:

```
Records: 600
Alerts:  152
Run ID:  ...
  markdown: examples/pathology_pipeline/out/reports/current_2026_04_15.md
  html:     examples/pathology_pipeline/out/reports/current_2026_04_15.html
  json:     examples/pathology_pipeline/out/reports/current_2026_04_15.json
```

Open the HTML report — it's organised exactly like a clinical model owner
would triage it: drift first, then calibration, subgroups, plausibility,
silent-failure signatures.

## 4. Persist runs

Add a `store:` line to your config to enable persistence-aware severity:

```yaml
store: ./biomodel.db
```

Re-run. Persistent alerts now bump severity automatically across runs.

## 5. Triage

```bash
biomodel-monitor incidents list --store ./biomodel.db \
    --model-id pathology-tumor-clf --model-version 1.0.0
```

Label one alert as a false positive:

```bash
biomodel-monitor incidents annotate --store ./biomodel.db \
    --model-id pathology-tumor-clf --model-version 1.0.0 \
    --key <alert-key> --kind label --label fp
```

## 6. Explain (v0.5)

```bash
biomodel-monitor explain --store ./biomodel.db \
    --model-id pathology-tumor-clf --model-version 1.0.0 \
    --metric psi
```

That tells you *when* the PSI series shifted, not just that it has.

## 7. Try a counterfactual

```bash
biomodel-monitor whatif \
    --input examples/pathology_pipeline/data/current_2026_04_15.csv \
    --exclude scanner_id=ScannerY
```

If `output_drift.after.psi.value` is dramatically lower than `before`,
ScannerY is the dominant contributor.

## 8. Generate a model card

```bash
biomodel-monitor model-card --store ./biomodel.db \
    --model-id pathology-tumor-clf --model-version 1.0.0 \
    --out model_card.md
```

## 9. Run the server

```bash
biomodel-monitor serve --store ./biomodel.db --api-key dev-key
```

Open `http://localhost:8080/docs` for live OpenAPI.
