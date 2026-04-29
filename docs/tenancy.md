# Multi-tenancy & RBAC (v0.7)

A single BioModel Monitor instance often serves many users — radiology and
pathology departments, model owners, MLOps engineers, an on-call rotation.
Treating every API key as equivalent quickly becomes a liability.

v0.7 introduces a **tenant model**. Each API key is mapped to a
`(tenant_id, role)` pair, and every mutation goes through a role check.

## Roles

| Role | Can do |
| ---- | ------ |
| `viewer` | Read endpoints — runs, alerts, metric history, model card, forecast, changepoints. |
| `operator` | Everything `viewer` can, **plus** annotate (ack / resolve / comment / label). |
| `writer` | Everything `operator` can, **plus** ingest, run pipeline, promote baselines. |
| `admin` | Everything `writer` can, **plus** view the audit log. |

## Configuration

Keys live in an environment variable so they can be sourced from a Secret
manager and never written to disk:

```bash
export BIOMODEL_TENANT_KEYS="\
key-aaa:radiology:writer,\
key-bbb:radiology:viewer,\
key-ccc:pathology:operator"
```

Or programmatically:

```python
from biomodel_monitor.server import AppSettings, create_app

app = create_app(AppSettings(
    tenant_keys={
        "key-aaa": ("radiology", "writer"),
        "key-bbb": ("radiology", "viewer"),
        "key-ccc": ("pathology", "operator"),
    },
    audit_log_path="/var/lib/biomodel/audit.jsonl",
))
```

## Identity & permissions

Clients can introspect what their key gets them:

```bash
curl -H "X-API-Key: key-bbb" https://monitor.example.com/tenants/whoami
```

```json
{
  "tenant_id": "radiology",
  "role": "viewer",
  "api_key_id": "-bbb",
  "permissions": ["list_runs", "list_alerts"]
}
```

## Audit log

When `BIOMODEL_AUDIT_LOG_PATH` is set, every annotation and baseline
promotion is appended to a SHA-256-chained JSONL file:

```bash
$ cat /var/lib/biomodel/audit.jsonl | jq -c
{"seq":0,"ts":"2026-04-29T20:00:00+00:00","actor_tenant":"radiology",
 "actor_role":"writer","actor_key_fp":"-aaa","action":"annotate",
 "payload":{"alert_key":"OUTPUT_DRIFT","model_id":"chest-xray-clf"},
 "prev_hash":"00...00","entry_hash":"a1f3..."}
```

To verify the chain:

```bash
biomodel-monitor audit-verify --path /var/lib/biomodel/audit.jsonl
# {"ok": true, "first_bad_seq": null, "n_entries": 142}
```

`/audit/verify` and `/audit/entries` expose the same checks over HTTP and
require the `admin` role.

## Backwards compatibility

If `BIOMODEL_TENANT_KEYS` is unset, the v0.4 single-API-key behaviour is
preserved. Authenticated callers are treated as the `default` tenant with
the `admin` role, so existing deployments keep working unchanged.
