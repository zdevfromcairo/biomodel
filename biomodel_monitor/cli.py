"""`biomodel-monitor` command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.alerts.threshold_tuner import propose_thresholds
from biomodel_monitor.baselines.learner import RollingBaselineLearner, promote_candidate
from biomodel_monitor.baselines.store import Baseline, BaselineStore
from biomodel_monitor.incidents.workspace import IncidentWorkspace
from biomodel_monitor.ingest.loader import load_batch, validate_against_contract
from biomodel_monitor.pipeline import run_pipeline, write_outputs
from biomodel_monitor.reports.regulatory import export_bundle, verify_bundle
from biomodel_monitor.scheduler.watcher import DirectoryWatcher, FilesystemQueue
from biomodel_monitor.store.repository import MetricsStore


def _load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    return json.loads(text)


def _open_store(cfg: dict) -> MetricsStore | None:
    store_cfg = cfg.get("store")
    if not store_cfg:
        return None
    if isinstance(store_cfg, str):
        return MetricsStore(store_cfg)
    if isinstance(store_cfg, dict) and store_cfg.get("path"):
        return MetricsStore(store_cfg["path"])
    return None


@click.group()
@click.version_option()
def main() -> None:
    """BioModel Monitor — batch + near-real-time monitoring for medical AI."""


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
@main.command("run")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def run_cmd(config_path: str) -> None:
    """Run the pipeline against a batch as described by a YAML/JSON config."""
    cfg = _load_config(config_path)
    batch_path = cfg.get("batch")
    if not batch_path:
        raise click.UsageError("config must contain 'batch: <path>'")
    batch = load_batch(batch_path)

    contract = cfg.get("contract")
    if contract:
        validate_against_contract(batch, contract)

    baseline: Baseline | None = None
    bcfg = cfg.get("baseline")
    if isinstance(bcfg, dict):
        if "path" in bcfg:
            store = BaselineStore(Path(bcfg["path"]).parent)
            baseline = store.load(
                batch.metadata.model_id,
                batch.metadata.model_version,
                bcfg.get("cohort"),
            )
            if baseline is None:
                bp = Path(bcfg["path"])
                if bp.exists():
                    data = json.loads(bp.read_text())
                    baseline = Baseline(**data)
        elif "from_batch" in bcfg:
            ref_batch = load_batch(bcfg["from_batch"])
            baseline = Baseline.from_batch(ref_batch, cohort=bcfg.get("cohort"))

    metrics_store = _open_store(cfg)

    # if a promoted baseline lives in the metrics store and no explicit baseline was
    # supplied, prefer that.
    if baseline is None and metrics_store is not None:
        promoted = metrics_store.get_promoted_baseline(
            model_id=batch.metadata.model_id,
            model_version=batch.metadata.model_version,
            cohort=(bcfg or {}).get("cohort") if isinstance(bcfg, dict) else None,
        )
        if promoted:
            keep = {"model_id", "model_version", "cohort", "n", "scores", "features",
                    "sites", "scanners", "stains", "tissue_types"}
            baseline = Baseline(**{k: promoted["payload"].get(k) for k in keep})

    alert_cfg = AlertConfig(
        min_severity=cfg.get("alerts", {}).get("min_severity", "warn"),
    )

    result = run_pipeline(
        batch,
        baseline=baseline,
        alert_config=alert_cfg,
        threshold=float(cfg.get("threshold", 0.5)),
        min_subgroup_n=int(cfg.get("min_subgroup_n", 30)),
        store=metrics_store,
    )
    out_dir = cfg.get("output_dir", "reports_out")
    paths = write_outputs(batch, result, out_dir=out_dir)
    click.echo(f"Records: {len(batch)}")
    click.echo(f"Alerts:  {len(result.alerts)}")
    if result.run_id:
        click.echo(f"Run ID:  {result.run_id}")
    for k, v in paths.items():
        click.echo(f"  {k}: {v}")
    if metrics_store is not None:
        metrics_store.close()


# --------------------------------------------------------------------------- #
# baseline (legacy, JSON-on-disk)
# --------------------------------------------------------------------------- #
@main.command("baseline")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True))
@click.option("--store-dir", required=True, type=click.Path())
@click.option("--cohort", default=None)
def baseline_cmd(input_path: str, store_dir: str, cohort: str | None) -> None:
    """Build and persist a baseline from a reference batch."""
    batch = load_batch(input_path)
    store = BaselineStore(store_dir)
    bl = Baseline.from_batch(batch, cohort=cohort)
    p = store.save(bl)
    click.echo(f"Saved baseline: {p}")


# --------------------------------------------------------------------------- #
# baseline-update / baseline-promote (rolling learner)
# --------------------------------------------------------------------------- #
@main.command("baseline-update")
@click.option("--store", "store_path", required=True, type=click.Path())
@click.option("--input", "input_path", required=True, type=click.Path(exists=True))
@click.option("--cohort", default=None)
@click.option("--site-id", "site_id", default=None)
@click.option("--max-window", default=5000, type=int)
def baseline_update_cmd(
    store_path: str, input_path: str, cohort: str | None,
    site_id: str | None, max_window: int,
) -> None:
    """Fold a new batch into the candidate baseline (rolling learner)."""
    store = MetricsStore(store_path)
    try:
        batch = load_batch(input_path)
        learner = RollingBaselineLearner(
            store=store,
            model_id=batch.metadata.model_id,
            model_version=batch.metadata.model_version,
            cohort=cohort, site_id=site_id, max_window=max_window,
        )
        payload = learner.update(batch)
        click.echo(f"Candidate baseline updated. n={payload['n']}")
    finally:
        store.close()


@main.command("baseline-promote")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--cohort", default=None)
@click.option("--site-id", "site_id", default=None)
@click.option("--min-n", default=200, type=int)
def baseline_promote_cmd(
    store_path: str, model_id: str, model_version: str,
    cohort: str | None, site_id: str | None, min_n: int,
) -> None:
    """Promote the most recent candidate baseline matching this scope."""
    store = MetricsStore(store_path)
    try:
        bid = promote_candidate(
            store=store, model_id=model_id, model_version=model_version,
            cohort=cohort, site_id=site_id, min_n=min_n,
        )
        click.echo(f"Promoted baseline id={bid}")
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# incidents (open / annotate)
# --------------------------------------------------------------------------- #
@main.group("incidents")
def incidents_group() -> None:
    """Annotate, label, ack, resolve, and review alerts."""


@incidents_group.command("list")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
def incidents_list_cmd(store_path: str, model_id: str, model_version: str) -> None:
    """Print open incidents, sorted by severity then persistence."""
    store = MetricsStore(store_path)
    try:
        ws = IncidentWorkspace(store)
        for s in ws.summarize_open(model_id=model_id, model_version=model_version):
            click.echo(
                f"[{s.severity.upper():5}] persistence={s.persistence} "
                f"score={s.score:.2f} key={s.key}  {s.title}"
            )
    finally:
        store.close()


@incidents_group.command("annotate")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--key", required=True, help="Alert key")
@click.option(
    "--kind",
    type=click.Choice(["ack", "resolve", "comment", "label"]),
    required=True,
)
@click.option("--label", default=None, type=click.Choice(["tp", "fp", "needs_review"]))
@click.option("--note", default=None)
@click.option("--actor", default=None)
def incidents_annotate_cmd(
    store_path: str, model_id: str, model_version: str, key: str,
    kind: str, label: str | None, note: str | None, actor: str | None,
) -> None:
    """Add an annotation to an alert."""
    store = MetricsStore(store_path)
    try:
        ws = IncidentWorkspace(store)
        if kind == "ack":
            ws.acknowledge(
                model_id=model_id, model_version=model_version,
                alert_key=key, actor=actor, note=note,
            )
        elif kind == "resolve":
            ws.resolve(
                model_id=model_id, model_version=model_version,
                alert_key=key, actor=actor, note=note,
            )
        elif kind == "comment":
            if not note:
                raise click.UsageError("--note is required for kind=comment")
            ws.comment(
                model_id=model_id, model_version=model_version,
                alert_key=key, note=note, actor=actor,
            )
        elif kind == "label":
            if not label:
                raise click.UsageError("--label is required for kind=label")
            ws.label(
                model_id=model_id, model_version=model_version,
                alert_key=key, label=label, actor=actor, note=note,
            )
        click.echo("ok")
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# tune-thresholds
# --------------------------------------------------------------------------- #
@main.command("tune-thresholds")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--target-recall", default=0.9, type=float)
def tune_thresholds_cmd(
    store_path: str, model_id: str, model_version: str, target_recall: float,
) -> None:
    """Propose per-category alert thresholds from labeled history."""
    store = MetricsStore(store_path)
    try:
        proposals = propose_thresholds(
            store, model_id=model_id, model_version=model_version,
            target_recall=target_recall,
        )
        click.echo(json.dumps([p.as_dict() for p in proposals], indent=2))
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# watch (near-real-time)
# --------------------------------------------------------------------------- #
@main.command("watch")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--directory", required=True, type=click.Path())
@click.option("--queue", "queue_path", required=True, type=click.Path())
@click.option("--max-iters", default=None, type=int, help="Bound the loop (testing).")
def watch_cmd(config_path: str, directory: str, queue_path: str, max_iters: int | None) -> None:
    """Watch a directory for new batches and append them to a filesystem queue.

    Pair this with ``biomodel-monitor process-queue`` running on the same queue
    file. The split keeps ingestion (cheap) decoupled from analysis (slow).
    """
    cfg = _load_config(config_path)  # noqa: F841 — reserved for future filters
    queue = FilesystemQueue(Path(queue_path))

    def on_event(ev) -> None:
        queue.enqueue(str(ev.path))
        click.echo(f"queued {ev.path}")

    watcher = DirectoryWatcher(Path(directory), on_event=on_event)
    if max_iters is None:
        watcher.run()
    else:
        watcher.run(max_iters=max_iters)


@main.command("process-queue")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--queue", "queue_path", required=True, type=click.Path())
@click.option("--max-items", default=None, type=int)
def process_queue_cmd(config_path: str, queue_path: str, max_items: int | None) -> None:
    """Drain a filesystem queue of batch files, running the pipeline on each."""
    cfg = _load_config(config_path)
    queue = FilesystemQueue(Path(queue_path))
    metrics_store = _open_store(cfg)
    n = 0
    try:
        while True:
            item = queue.claim()
            if item is None:
                break
            try:
                batch = load_batch(item)
                run_pipeline(batch, store=metrics_store)
                queue.acknowledge(item)
                click.echo(f"processed {item}")
                n += 1
            except Exception as e:  # noqa: BLE001
                queue.fail(item)
                click.echo(f"failed {item}: {e}", err=True)
            if max_items is not None and n >= max_items:
                break
    finally:
        if metrics_store is not None:
            metrics_store.close()
    click.echo(f"processed {n} item(s)")


# --------------------------------------------------------------------------- #
# export-bundle / verify-bundle
# --------------------------------------------------------------------------- #
@main.command("export-bundle")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--report-dir", required=True, type=click.Path(exists=True))
@click.option("--out-dir", required=True, type=click.Path())
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--batch-id", required=True)
def export_bundle_cmd(
    store_path: str, report_dir: str, out_dir: str,
    model_id: str, model_version: str, batch_id: str,
) -> None:
    """Build a regulatory-export bundle (report + audit trail + manifest)."""
    rd = Path(report_dir)
    paths: dict[str, Path] = {}
    for ext in (".html", ".md", ".json"):
        candidate = rd / f"{batch_id}{ext}"
        if candidate.exists():
            paths[ext.lstrip(".")] = candidate
    if not paths:
        raise click.UsageError(
            f"no report files found for batch {batch_id} in {report_dir}"
        )
    store = MetricsStore(store_path)
    try:
        bundle = export_bundle(
            out_dir=out_dir, report_paths=paths,
            model_id=model_id, model_version=model_version,
            batch_id=batch_id, store=store,
        )
    finally:
        store.close()
    click.echo(f"bundle: {bundle}")


@main.command("verify-bundle")
@click.argument("bundle_dir", type=click.Path(exists=True))
def verify_bundle_cmd(bundle_dir: str) -> None:
    ok, problems = verify_bundle(bundle_dir)
    if ok:
        click.echo("OK")
    else:
        for p in problems:
            click.echo(p, err=True)
        raise click.exceptions.Exit(1)


# --------------------------------------------------------------------------- #
# serve (v0.4)
# --------------------------------------------------------------------------- #
@main.command("serve")
@click.option("--store", "store_path", required=True, type=click.Path())
@click.option("--host", default="0.0.0.0")  # noqa: S104 — explicit binding
@click.option("--port", default=8080, type=int)
@click.option("--api-key", "api_keys", multiple=True, help="Repeatable. Required unless --no-auth.")
@click.option("--no-auth", is_flag=True, help="Disable API-key auth (development only).")
@click.option("--cors", "cors", multiple=True, help="Allowed CORS origin (repeatable).")
@click.option(
    "--batch-root", "batch_root", default=None, type=click.Path(),
    help="Whitelisted directory for batch-path inputs to /runs and /whatif.",
)
@click.option("--log-level", default="INFO")
def serve_cmd(
    store_path: str, host: str, port: int,
    api_keys: tuple[str, ...], no_auth: bool, cors: tuple[str, ...],
    batch_root: str | None, log_level: str,
) -> None:
    """Start the BioModel Monitor HTTP API server (FastAPI + uvicorn)."""
    from biomodel_monitor.server import AppSettings, run_uvicorn
    if not no_auth and not api_keys:
        raise click.UsageError("Must supply at least one --api-key, or pass --no-auth.")
    settings = AppSettings(
        store_path=store_path, api_keys=list(api_keys),
        require_auth=not no_auth, cors_origins=list(cors), log_level=log_level,
        batch_root=batch_root,
    )
    click.echo(f"Serving on http://{host}:{port}  (auth={'on' if not no_auth else 'OFF'})")
    run_uvicorn(settings, host=host, port=port)


# --------------------------------------------------------------------------- #
# model-card (v0.5)
# --------------------------------------------------------------------------- #
@main.command("model-card")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--out", "out_path", required=False, type=click.Path())
def model_card_cmd(
    store_path: str, model_id: str, model_version: str, out_path: str | None,
) -> None:
    """Generate a Markdown model card from the persistent store."""
    from biomodel_monitor.intelligence.modelcard import build_model_card
    store = MetricsStore(store_path)
    try:
        text = build_model_card(store, model_id=model_id, model_version=model_version)
    finally:
        store.close()
    if out_path:
        Path(out_path).write_text(text)
        click.echo(f"wrote {out_path}")
    else:
        click.echo(text)


# --------------------------------------------------------------------------- #
# explain (v0.5)
# --------------------------------------------------------------------------- #
@main.command("explain")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--metric", required=True, help="Metric name to explain (e.g. psi).")
def explain_cmd(
    store_path: str, model_id: str, model_version: str, metric: str,
) -> None:
    """Run changepoint + anomaly analysis on a metric's history."""
    from biomodel_monitor.intelligence.anomaly import robust_zscore
    from biomodel_monitor.intelligence.changepoint import detect_changepoints
    store = MetricsStore(store_path)
    try:
        rows = store.metric_history(
            model_id=model_id, model_version=model_version, name=metric, limit=500,
        )
    finally:
        store.close()
    series = [float(r["value"]) for r in rows if r.get("value") is not None]
    out = {
        "metric": metric,
        "n_points": len(series),
        "changepoints": detect_changepoints(series).as_dict(),
        "anomaly": robust_zscore(series).as_dict() if series else None,
    }
    click.echo(json.dumps(out, indent=2))


# --------------------------------------------------------------------------- #
# whatif (v0.5)
# --------------------------------------------------------------------------- #
@main.command("whatif")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True))
@click.option("--baseline", "baseline_path", required=False, type=click.Path(exists=True))
@click.option("--exclude", "excludes", multiple=True,
              help="dim=value pair to exclude. Repeatable. e.g. --exclude scanner_id=ScannerY")
def whatif_cmd(input_path: str, baseline_path: str | None, excludes: tuple[str, ...]) -> None:
    """Recompute drift after counterfactually excluding cohorts/sites/scanners."""
    from biomodel_monitor.intelligence.whatif import counterfactual_drift
    batch = load_batch(input_path)
    baseline = None
    if baseline_path:
        data = json.loads(Path(baseline_path).read_text())
        baseline = Baseline(**data)
    exclude_map: dict[str, list[str]] = {}
    for e in excludes:
        if "=" not in e:
            raise click.UsageError(f"--exclude must be dim=value, got: {e}")
        dim, val = e.split("=", 1)
        exclude_map.setdefault(dim.strip(), []).append(val.strip())
    out = counterfactual_drift(batch, baseline, exclude=exclude_map)
    click.echo(json.dumps(out, indent=2, default=str))


@main.command("forecast")
@click.option("--store", "store_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--metric", required=True)
@click.option("--horizon", default=10, type=int)
@click.option("--threshold", default=None, type=float)
@click.option("--direction", default="above",
              type=click.Choice(["above", "below"]))
def forecast_cmd(
    store_path: str, model_id: str, model_version: str,
    metric: str, horizon: int, threshold: float | None, direction: str,
) -> None:
    """Forecast a stored metric series and project ETA to a threshold (v0.6)."""
    from biomodel_monitor.metrics.forecast import forecast_metric
    from biomodel_monitor.store.repository import MetricsStore
    store = MetricsStore(store_path)
    try:
        history = store.metric_history(
            model_id=model_id, model_version=model_version, name=metric, limit=200,
        )
    finally:
        store.close()
    values = [float(p["value"]) for p in history if p.get("value") is not None]
    if not values:
        raise click.UsageError(f"no history for metric '{metric}'")
    result = forecast_metric(
        values, metric_name=metric, horizon=horizon,
        threshold=threshold, direction=direction,  # type: ignore[arg-type]
    )
    click.echo(json.dumps(result.as_dict(), indent=2, default=str))


@main.command("ingest")
@click.option("--server", "server_url", required=True,
              help="BioModel Monitor server URL.")
@click.option("--api-key", "api_key", default=None)
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--input", "input_path", required=True, type=click.Path(exists=True),
              help="JSON or JSONL file containing prediction records.")
@click.option("--flush", is_flag=True, help="Force-flush the server window after upload.")
def ingest_cmd(
    server_url: str, api_key: str | None, model_id: str, model_version: str,
    input_path: str, flush: bool,
) -> None:
    """Stream records from a file into the server's micro-batching window (v0.6)."""
    from biomodel_monitor.client import BioModelMonitorClient
    text = Path(input_path).read_text()
    records: list[dict]
    if input_path.endswith(".jsonl"):
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        loaded = json.loads(text)
        records = loaded if isinstance(loaded, list) else [loaded]
    client = BioModelMonitorClient(server_url, api_key=api_key)
    out = client.ingest(model_id, model_version, records, flush=flush)
    click.echo(json.dumps(out, indent=2, default=str))


@main.command("client-call")
@click.option("--server", "server_url", required=True)
@click.option("--api-key", "api_key", default=None)
@click.option("--method", default="GET",
              type=click.Choice(["GET", "POST"], case_sensitive=False))
@click.argument("path")
@click.option("--json-body", "json_body", default=None, type=str,
              help="Optional JSON request body.")
def client_call_cmd(
    server_url: str, api_key: str | None, method: str, path: str,
    json_body: str | None,
) -> None:
    """Make a raw call against the server using the bundled SDK (v0.6)."""
    from biomodel_monitor.client import BioModelMonitorClient
    client = BioModelMonitorClient(server_url, api_key=api_key)
    body = json.loads(json_body) if json_body else None
    out = client.request(method.upper(), path, json_body=body)
    click.echo(json.dumps(out, indent=2, default=str))


@main.group("tenant")
def tenant_grp() -> None:
    """Inspect tenant API-key registry (v0.7)."""


@tenant_grp.command("list")
def tenant_list_cmd() -> None:
    """List tenants resolvable from the BIOMODEL_TENANT_KEYS env var."""
    from biomodel_monitor.tenancy import TenantRegistry
    reg = TenantRegistry.from_env()
    if len(reg) == 0:
        click.echo("No tenants configured. Set BIOMODEL_TENANT_KEYS, e.g.:")
        click.echo("  export BIOMODEL_TENANT_KEYS='key1:hospA:writer,key2:hospB:viewer'")
        return
    rows = [
        {"key_fp": k[-4:] if len(k) >= 4 else "****", "tenant_id": v.tenant_id,
         "role": v.role}
        for k, v in reg.entries.items()
    ]
    click.echo(json.dumps(rows, indent=2))


@main.group("plugins")
def plugins_grp() -> None:
    """Inspect installed plugins (v0.7)."""


@plugins_grp.command("list")
def plugins_list_cmd() -> None:
    """List plugins discovered from Python entry points."""
    from biomodel_monitor.plugins import discover_plugins
    reg = discover_plugins()
    rows = [{"name": p.name, "group": p.group, "source": p.source,
             "metadata": p.metadata} for p in reg.list()]
    click.echo(json.dumps(rows, indent=2, default=str))


@main.command("federate")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True),
              help="JSON file containing {kind, summaries: [...]}.")
def federate_cmd(input_path: str) -> None:
    """Aggregate per-site sufficient statistics across sites (v0.7).

    Input file shape::

        {"kind": "drift",  "summaries": [HistogramSummary, ...]}
        {"kind": "calibration", "summaries": [CalibrationSummary, ...]}
        {"kind": "moments", "summaries": [MomentSummary, ...]}
    """
    from biomodel_monitor.federated import (
        CalibrationSummary,
        HistogramSummary,
        MomentSummary,
        aggregate_calibration,
        aggregate_histograms,
        aggregate_moments,
    )
    body = json.loads(Path(input_path).read_text())
    kind = body.get("kind")
    raw = body.get("summaries", [])
    if kind == "drift":
        out = aggregate_histograms([HistogramSummary(**s) for s in raw])
    elif kind == "calibration":
        out = aggregate_calibration([CalibrationSummary(**s) for s in raw])
    elif kind == "moments":
        out = aggregate_moments([MomentSummary(**s) for s in raw])
    else:
        raise click.UsageError(
            f"unknown kind {kind!r}; must be one of drift, calibration, moments")
    click.echo(json.dumps(out.as_dict(), indent=2, default=str))


@main.command("audit-verify")
@click.option("--path", "audit_path", required=True, type=click.Path(exists=True))
def audit_verify_cmd(audit_path: str) -> None:
    """Re-walk an audit-log JSONL file and report integrity (v0.7)."""
    from biomodel_monitor.audit import AuditLog
    log = AuditLog(audit_path)
    ok, bad = log.verify()
    click.echo(json.dumps({"ok": ok, "first_bad_seq": bad,
                           "n_entries": len(log.entries())}, indent=2))


# --------------------------------------------------------------------------- #
# v0.8 commands
# --------------------------------------------------------------------------- #

@main.command("pipeline-async")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--batch", "batch_path", required=True, type=click.Path(exists=True))
@click.option("--out", "out_dir", required=True, type=click.Path())
@click.option("--workers", default=4, show_default=True, type=int)
@click.option("--threshold", default=0.5, show_default=True, type=float)
@click.option("--min-subgroup-n", default=30, show_default=True, type=int)
def pipeline_async_cmd(
    config_path: str, batch_path: str, out_dir: str,
    workers: int, threshold: float, min_subgroup_n: int,
) -> None:
    """Run the v0.8 concurrent pipeline and write reports."""
    from biomodel_monitor.baselines.store import Baseline, BaselineStore  # noqa: F401
    from biomodel_monitor.pipeline import write_outputs
    from biomodel_monitor.pipeline_async import run_pipeline_async

    cfg = _load_config(config_path)
    batch = load_batch(batch_path)
    baseline: Baseline | None = None
    if cfg.get("baseline_path"):
        bs = BaselineStore(cfg["baseline_path"])
        cohort = cfg.get("cohort")
        baseline = bs.get_active(model_id=batch.metadata.model_id,
                                 model_version=batch.metadata.model_version,
                                 cohort=cohort)
    store = _open_store(cfg)
    result, stats = run_pipeline_async(
        batch, baseline=baseline, store=store,
        threshold=threshold, min_subgroup_n=min_subgroup_n, workers=workers,
    )
    paths = write_outputs(batch, result, out_dir=out_dir)
    click.echo(json.dumps({
        "n_alerts": len(result.alerts),
        "stats": stats.as_dict(),
        "outputs": {k: str(v) for k, v in paths.items()},
    }, indent=2))


@main.command("mmd")
@click.option("--reference", "reference_path", required=True, type=click.Path(exists=True),
              help="JSON file: list of vectors (or scalars).")
@click.option("--current", "current_path", required=True, type=click.Path(exists=True))
@click.option("--n-permutations", default=200, show_default=True, type=int)
@click.option("--bandwidth", default=None, type=float)
def mmd_cmd(reference_path: str, current_path: str,
            n_permutations: int, bandwidth: float | None) -> None:
    """Embedding-drift MMD between two saved JSON arrays (v0.8)."""
    from biomodel_monitor.metrics.embedding_drift import mmd_rbf
    ref = json.loads(Path(reference_path).read_text())
    cur = json.loads(Path(current_path).read_text())
    res = mmd_rbf(ref, cur, n_permutations=n_permutations, bandwidth=bandwidth)
    click.echo(json.dumps(res.as_dict(), indent=2))


@main.command("cusum")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True),
              help="JSON file: list of floats (the metric series).")
@click.option("--target", default=None, type=float)
@click.option("--sigma", default=None, type=float)
@click.option("--threshold", default=4.0, show_default=True, type=float)
@click.option("--slack-k", default=0.5, show_default=True, type=float)
def cusum_cmd(input_path: str, target: float | None, sigma: float | None,
              threshold: float, slack_k: float) -> None:
    """Run an offline CUSUM over a saved metric series (v0.8)."""
    from biomodel_monitor.metrics.cusum import cusum_offline
    series = json.loads(Path(input_path).read_text())
    res = cusum_offline(series, target=target, sigma=sigma,
                        threshold=threshold, slack_k=slack_k)
    click.echo(json.dumps(res.as_dict(), indent=2))


@main.command("drift-graph")
@click.option("--batch", "batch_path", required=True, type=click.Path(exists=True))
@click.option("--out", "out_path", default=None, type=click.Path(),
              help="If set, write a Graphviz .dot file to this path.")
@click.option("--min-n", default=20, show_default=True, type=int)
def drift_graph_cmd(batch_path: str, out_path: str | None, min_n: int) -> None:
    """Build the dimension-influence drift graph for a batch (v0.8)."""
    from biomodel_monitor.intelligence.drift_graph import build_drift_graph
    batch = load_batch(batch_path)
    g = build_drift_graph(batch.records, min_n=min_n)
    if out_path:
        Path(out_path).write_text(g.to_dot())
    click.echo(json.dumps(g.as_dict(), indent=2, default=str))


@main.command("events-tail")
@click.option("--server", "base_url", default="http://127.0.0.1:8080",
              show_default=True)
@click.option("--api-key", "api_key", required=True, envvar="BIOMODEL_API_KEY")
@click.option("--limit", default=20, show_default=True, type=int)
def events_tail_cmd(base_url: str, api_key: str, limit: int) -> None:
    """Print the last N events from a running BioModel Monitor server (v0.8)."""
    from biomodel_monitor.client import BioModelMonitorClient
    client = BioModelMonitorClient(base_url, api_key=api_key)
    rows = client.events(limit=limit)
    for r in rows:
        click.echo(json.dumps(r, default=str))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


# --------------------------------------------------------------------------- #
# v0.9 — registry, policy, sbom, wasserstein, canary
# --------------------------------------------------------------------------- #


@main.group("registry")
def registry_grp() -> None:
    """Model registry: register, list, quarantine, lineage (v0.9)."""


@registry_grp.command("register")
@click.option("--db", "db_path", required=True, type=click.Path())
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--training-data-hash", default=None)
@click.option("--framework", default=None)
@click.option("--notes", default=None)
def registry_register_cmd(db_path: str, model_id: str, model_version: str,
                          training_data_hash: str | None,
                          framework: str | None, notes: str | None) -> None:
    """Register a model version in the registry."""
    from biomodel_monitor.registry import ModelRecord, ModelRegistry
    reg = ModelRegistry(db_path)
    rec = reg.register(ModelRecord(
        model_id=model_id, model_version=model_version,
        training_data_hash=training_data_hash, framework=framework,
        notes=notes,
    ))
    click.echo(json.dumps(rec.as_dict(), indent=2, default=str))
    reg.close()


@registry_grp.command("list")
@click.option("--db", "db_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", default=None)
@click.option("--status", type=click.Choice(["active", "quarantined", "retired"]),
              default=None)
def registry_list_cmd(db_path: str, model_id: str | None,
                      status: str | None) -> None:
    """List models in the registry."""
    from biomodel_monitor.registry import ModelRegistry
    reg = ModelRegistry(db_path)
    rows = reg.list(model_id=model_id, status=status)  # type: ignore[arg-type]
    for r in rows:
        click.echo(json.dumps(r.as_dict(), default=str))
    reg.close()


@registry_grp.command("quarantine")
@click.option("--db", "db_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
@click.option("--note", default=None)
def registry_quarantine_cmd(db_path: str, model_id: str, model_version: str,
                            note: str | None) -> None:
    """Quarantine a model version."""
    from biomodel_monitor.registry import ModelRegistry
    reg = ModelRegistry(db_path)
    rec = reg.quarantine(model_id, model_version, note=note)
    click.echo(json.dumps(rec.as_dict(), indent=2, default=str))
    reg.close()


@registry_grp.command("lineage")
@click.option("--db", "db_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", required=True)
@click.option("--model-version", required=True)
def registry_lineage_cmd(db_path: str, model_id: str, model_version: str) -> None:
    """Print upstream/downstream lineage for a model version."""
    from biomodel_monitor.registry import ModelRegistry
    reg = ModelRegistry(db_path)
    out = {
        "upstreams": [e.as_dict() for e in reg.upstreams(model_id, model_version)],
        "downstreams": [e.as_dict() for e in reg.downstreams(model_id, model_version)],
    }
    click.echo(json.dumps(out, indent=2, default=str))
    reg.close()


@main.command("policy-eval")
@click.option("--policies", "policy_path", required=True, type=click.Path(exists=True))
@click.option("--alerts", "alerts_path", required=True, type=click.Path(exists=True))
@click.option("--model-id", default=None)
@click.option("--model-version", default=None)
def policy_eval_cmd(policy_path: str, alerts_path: str,
                    model_id: str | None, model_version: str | None) -> None:
    """Evaluate declarative governance policies against a list of alerts (v0.9)."""
    from biomodel_monitor.policy import PolicyEngine
    eng = PolicyEngine.from_yaml(Path(policy_path).read_text())
    alerts = json.loads(Path(alerts_path).read_text())
    actions = eng.evaluate(alerts, model_id=model_id, model_version=model_version)
    for a in actions:
        click.echo(json.dumps(a.as_dict(), default=str))


@main.command("wasserstein")
@click.option("--reference", "reference_path", required=True, type=click.Path(exists=True))
@click.option("--current", "current_path", required=True, type=click.Path(exists=True))
@click.option("--projections", default=64, show_default=True, type=int)
@click.option("--seed", default=0, show_default=True, type=int)
def wasserstein_cmd(reference_path: str, current_path: str,
                    projections: int, seed: int) -> None:
    """Sliced 1-D Wasserstein-1 distance between two embedding sets (v0.9)."""
    from biomodel_monitor.metrics.wasserstein import sliced_wasserstein
    ref = json.loads(Path(reference_path).read_text())
    cur = json.loads(Path(current_path).read_text())
    res = sliced_wasserstein(ref, cur, n_projections=projections, seed=seed)
    click.echo(json.dumps(res.as_dict(), indent=2, default=str))


@main.command("sbom")
@click.option("--out", "out_path", default=None, type=click.Path())
def sbom_cmd(out_path: str | None) -> None:
    """Emit a CycloneDX-1.5 SBOM for the running environment (v0.9)."""
    from biomodel_monitor.security import build_sbom
    body = json.dumps(build_sbom(), indent=2, default=str)
    if out_path:
        Path(out_path).write_text(body)
        click.echo(f"wrote SBOM to {out_path}")
    else:
        click.echo(body)


@main.command("canary")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True),
              help="JSON file with {control: [...], canary: [...]} score arrays.")
@click.option("--alpha", default=0.01, show_default=True, type=float)
@click.option("--tau", default=0.1, show_default=True, type=float)
@click.option("--min-n", default=30, show_default=True, type=int)
def canary_cmd(input_path: str, alpha: float, tau: float, min_n: int) -> None:
    """Sequential A/B canary verdict via mSPRT (v0.9)."""
    from biomodel_monitor.canary import CanaryMonitor
    body = json.loads(Path(input_path).read_text())
    mon = CanaryMonitor(alpha=alpha, tau=tau, min_n=min_n)
    for x in body.get("control", []):
        mon.add_control(float(x))
    for x in body.get("canary", []):
        mon.add_canary(float(x))
    click.echo(json.dumps(mon.decide().as_dict(), indent=2, default=str))
