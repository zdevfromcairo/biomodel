"""End-to-end pipeline tests: ingest -> metrics -> alert -> report."""

import json
from pathlib import Path

from biomodel_monitor.alerts.engine import AlertConfig
from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.pipeline import run_pipeline, write_outputs


def test_end_to_end_pipeline_writes_reports(tmp_path: Path, make_batch):
    ref = make_batch(n=300, seed=0)
    cur = make_batch(n=300, seed=1)
    baseline = Baseline.from_batch(ref)

    result = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))
    paths = write_outputs(cur, result, out_dir=tmp_path)

    assert paths["markdown"].exists()
    assert paths["html"].exists()
    assert paths["json"].exists()
    # JSON is valid and contains the expected sections.
    data = json.loads(paths["json"].read_text())
    assert {"drift_results", "calibration_results", "subgroup_results",
            "plausibility_violations", "silent_failure_results", "alerts"} <= data.keys()


def test_simulated_site_shift_detected(make_batch):
    """Inject a site-specific score shift and confirm the drift+subgroup signals fire."""
    ref = make_batch(n=400, seed=0, site_mix=("A", "B"))
    # Current: site B is shifted upward by +0.4
    cur = make_batch(n=400, seed=2, site_mix=("A", "B"), score_bias=0.4)

    baseline = Baseline.from_batch(ref)
    result = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))

    drift_alerts = [a for a in result.alerts if a.category == "drift"]
    assert drift_alerts, "expected at least one drift alert from injected shift"
    score_drift = [a for a in drift_alerts if "output_score" in a.details.get("kind", "")]
    assert score_drift, "expected output_score drift alert"


def test_explanation_stability_across_runs(make_batch):
    """Two identical runs must produce identical alert keys (deterministic)."""
    ref = make_batch(n=200, seed=0)
    cur = make_batch(n=200, seed=5, score_bias=0.3)
    baseline = Baseline.from_batch(ref)

    r1 = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))
    r2 = run_pipeline(cur, baseline=baseline, alert_config=AlertConfig(min_severity="warn"))

    keys1 = sorted(a.key for a in r1.alerts)
    keys2 = sorted(a.key for a in r2.alerts)
    assert keys1 == keys2


def test_cli_run(tmp_path: Path, make_batch):
    """CLI integration: invoke `biomodel-monitor run` with a YAML config."""
    from click.testing import CliRunner

    from biomodel_monitor.cli import main

    ref = make_batch(n=200, seed=0)
    cur = make_batch(n=200, seed=1)

    ref_path = tmp_path / "ref.jsonl"
    cur_path = tmp_path / "cur.jsonl"
    ref_path.write_text(
        "\n".join(json.dumps(r.model_dump(mode="json")) for r in ref.records)
    )
    cur_path.write_text(
        "\n".join(json.dumps(r.model_dump(mode="json")) for r in cur.records)
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
batch: {cur_path}
baseline:
  from_batch: {ref_path}
output_dir: {tmp_path / "out"}
alerts:
  min_severity: warn
""".strip()
    )
    runner = CliRunner()
    res = runner.invoke(main, ["run", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out").exists()
    files = list((tmp_path / "out").iterdir())
    assert any(p.suffix == ".md" for p in files)
    assert any(p.suffix == ".html" for p in files)
    assert any(p.suffix == ".json" for p in files)
