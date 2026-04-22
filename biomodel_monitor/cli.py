"""`biomodel-monitor` command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline, BaselineStore
from biomodel_monitor.ingest.loader import load_batch, validate_against_contract
from biomodel_monitor.pipeline import run_pipeline, write_outputs


def _load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    return json.loads(text)


@click.group()
@click.version_option()
def main() -> None:
    """BioModel Monitor — offline batch monitoring for medical AI."""


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
                # Fall back to literal file if precise model+cohort not found
                bp = Path(bcfg["path"])
                if bp.exists():
                    data = json.loads(bp.read_text())
                    baseline = Baseline(**data)
        elif "from_batch" in bcfg:
            ref_batch = load_batch(bcfg["from_batch"])
            baseline = Baseline.from_batch(ref_batch, cohort=bcfg.get("cohort"))

    alert_cfg = AlertConfig(
        min_severity=cfg.get("alerts", {}).get("min_severity", "warn"),
    )

    result = run_pipeline(
        batch,
        baseline=baseline,
        alert_config=alert_cfg,
        threshold=float(cfg.get("threshold", 0.5)),
        min_subgroup_n=int(cfg.get("min_subgroup_n", 30)),
    )
    out_dir = cfg.get("output_dir", "reports_out")
    paths = write_outputs(batch, result, out_dir=out_dir)
    click.echo(f"Records: {len(batch)}")
    click.echo(f"Alerts:  {len(result.alerts)}")
    for k, v in paths.items():
        click.echo(f"  {k}: {v}")


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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
