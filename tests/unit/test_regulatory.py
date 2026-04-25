"""Tests for the regulatory export bundle."""

from __future__ import annotations

import json

from biomodel_monitor.reports.regulatory import export_bundle, verify_bundle


def test_bundle_round_trip(tmp_path):
    rd = tmp_path / "reports"
    rd.mkdir()
    (rd / "b1.html").write_text("<html>ok</html>")
    (rd / "b1.md").write_text("# ok")
    (rd / "b1.json").write_text(json.dumps({"alerts": []}))

    bundle = export_bundle(
        out_dir=tmp_path / "out",
        report_paths={"html": rd / "b1.html", "md": rd / "b1.md", "json": rd / "b1.json"},
        model_id="m", model_version="1.0.0", batch_id="b1",
    )
    assert (bundle / "manifest.json").exists()
    assert (bundle / "b1.html").exists()
    ok, problems = verify_bundle(bundle)
    assert ok and problems == []


def test_bundle_detects_tampering(tmp_path):
    rd = tmp_path / "reports"
    rd.mkdir()
    (rd / "b1.html").write_text("clean")
    bundle = export_bundle(
        out_dir=tmp_path / "out",
        report_paths={"html": rd / "b1.html"},
        model_id="m", model_version="1.0.0", batch_id="b1",
    )
    # tamper
    (bundle / "b1.html").write_text("MODIFIED")
    ok, problems = verify_bundle(bundle)
    assert not ok and any("hash mismatch" in p for p in problems)
