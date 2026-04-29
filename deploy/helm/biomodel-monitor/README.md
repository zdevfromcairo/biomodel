# BioModel Monitor — Helm chart

A production-leaning Helm chart for the BioModel Monitor server (v0.4+
FastAPI app + v0.6 streaming + v0.7 multi-tenancy).

## TL;DR

```bash
helm install monitor ./deploy/helm/biomodel-monitor \
  --set image.tag=0.7.0 \
  --set apiKeys[0]="$(openssl rand -hex 24)" \
  --set env.BIOMODEL_TENANT_KEYS="key1:hospA:writer,key2:hospB:viewer"
```

## What it deploys

| Resource              | When |
| --------------------- | ---- |
| `Deployment`          | always |
| `Service`             | always |
| `PersistentVolumeClaim` | when `persistence.enabled` (default) |
| `Secret` (API keys)   | when `apiKeys` is non-empty |
| `Ingress`             | when `ingress.enabled` |

## Notable values

| Key | Default | Purpose |
| --- | ------- | ------- |
| `env.BIOMODEL_TENANT_KEYS` | `""` | v0.7 multi-tenant API keys. |
| `env.BIOMODEL_AUDIT_LOG_PATH` | `/var/lib/biomodel/audit.jsonl` | v0.7 tamper-evident audit log. |
| `env.BIOMODEL_DISCOVER_PLUGINS` | `"false"` | v0.7 entry-point plugin discovery. |
| `env.BIOMODEL_STREAM_MAX_RECORDS` | `"256"` | v0.6 micro-batching window size. |
| `persistence.size` | `5Gi` | PVC for the SQLite store and audit log. |

See `values.yaml` for the full list.
