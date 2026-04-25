"""Regulatory / QMS export pack.

Bundles a run's report, JSON metrics, alert audit trail (annotations), and a
manifest with content hashes into a single directory suitable for archival.
The manifest is the canonical evidence artifact: it records each file's
SHA-256 so a reviewer can detect post-hoc edits.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from biomodel_monitor import __version__
from biomodel_monitor.store.repository import MetricsStore


@dataclass
class ExportManifest:
    bundle_id: str
    created_at: str
    model_id: str
    model_version: str
    batch_id: str
    run_id: str | None
    monitor_version: str
    file_hashes: dict[str, str] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "created_at": self.created_at,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "batch_id": self.batch_id,
            "run_id": self.run_id,
            "monitor_version": self.monitor_version,
            "file_hashes": self.file_hashes,
            "metadata": self.metadata,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def export_bundle(
    *,
    out_dir: Path | str,
    report_paths: dict[str, Path],
    model_id: str,
    model_version: str,
    batch_id: str,
    run_id: str | None = None,
    store: MetricsStore | None = None,
    metadata: dict | None = None,
) -> Path:
    """Materialise a regulatory export bundle.

    Returns the path to the bundle directory; the manifest is written as
    ``manifest.json`` inside it.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_id = f"{batch_id}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    bundle = out / bundle_id
    bundle.mkdir(parents=True, exist_ok=True)

    file_hashes: dict[str, str] = {}
    for src in report_paths.values():
        src = Path(src)
        dst = bundle / src.name
        shutil.copy2(src, dst)
        file_hashes[src.name] = _sha256(dst)

    if store is not None:
        # alerts + annotations for this batch's run(s)
        runs = store.list_runs(model_id=model_id, model_version=model_version, limit=200)
        run_alerts: list[dict] = []
        annotations: list[dict] = []
        for r in runs:
            if r.batch_id != batch_id:
                continue
            for a in store.list_alerts(run_id=r.run_id):
                run_alerts.append({
                    "key": a.key, "title": a.title, "severity": a.severity,
                    "score": a.score, "category": a.category,
                    "root_cause_hint": a.root_cause_hint,
                    "persistence": a.persistence, "created_at": a.created_at,
                })
                for ann in store.list_annotations(
                    alert_key=a.key, model_id=model_id, model_version=model_version,
                ):
                    annotations.append({
                        "alert_key": ann.alert_key, "kind": ann.kind,
                        "label": ann.label, "note": ann.note,
                        "actor": ann.actor, "created_at": ann.created_at,
                    })
        audit_path = bundle / "audit_trail.json"
        audit_path.write_text(json.dumps(
            {"alerts": run_alerts, "annotations": annotations}, indent=2,
        ))
        file_hashes[audit_path.name] = _sha256(audit_path)

    manifest = ExportManifest(
        bundle_id=bundle_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model_id=model_id, model_version=model_version,
        batch_id=batch_id, run_id=run_id,
        monitor_version=__version__,
        file_hashes=file_hashes,
        metadata=metadata or {},
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest.as_dict(), indent=2))
    return bundle


def verify_bundle(bundle_dir: Path | str) -> tuple[bool, list[str]]:
    """Recompute SHA-256 for each file in the manifest. Returns (ok, mismatches)."""
    bundle = Path(bundle_dir)
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        return False, ["manifest.json missing"]
    manifest = json.loads(manifest_path.read_text())
    bad: list[str] = []
    for name, expected in manifest.get("file_hashes", {}).items():
        p = bundle / name
        if not p.exists():
            bad.append(f"{name}: missing")
            continue
        actual = _sha256(p)
        if actual != expected:
            bad.append(f"{name}: hash mismatch")
    return (not bad), bad
