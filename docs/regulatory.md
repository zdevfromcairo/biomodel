# Regulatory export

Auditors and QMS systems need a self-contained, tamper-evident bundle.
`biomodel-monitor export-bundle` produces exactly that.

## Build a bundle

```bash
biomodel-monitor export-bundle \
    --store ./biomodel.db \
    --report-dir ./reports_out \
    --out-dir ./exports \
    --model-id pathology-tumor-clf \
    --model-version 1.0.0 \
    --batch-id batch_2026_04_15
```

Produces `exports/batch_2026_04_15__YYYYMMDDTHHMMSSZ/` containing:

| File                | Contents                                                  |
| ------------------- | --------------------------------------------------------- |
| `report.html`       | Operator-facing report                                    |
| `report.md`         | Markdown twin                                             |
| `report.json`       | Machine-readable metrics                                  |
| `audit_trail.json`  | All annotations (ack / resolve / comment / label)         |
| `manifest.json`     | SHA-256 of every file, plus model + batch metadata        |

## Verify a bundle

```bash
biomodel-monitor verify-bundle exports/batch_2026_04_15__20260415T120000Z
```

Re-hashes every file and compares against the manifest. Exits non-zero on any
mismatch — perfect for CI gates before submission.

## What's in the manifest

```json
{
  "model_id": "pathology-tumor-clf",
  "model_version": "1.0.0",
  "batch_id": "batch_2026_04_15",
  "exported_at": "2026-04-15T12:00:00Z",
  "files": [
    {"path": "report.html", "sha256": "..."},
    {"path": "audit_trail.json", "sha256": "..."}
  ],
  "schema_version": 1
}
```
