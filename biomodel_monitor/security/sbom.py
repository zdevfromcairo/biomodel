"""Software Bill of Materials + SLSA-style provenance (v0.9).

Two small generators that produce regulator-friendly artefacts:

* :func:`build_sbom` — a CycloneDX-1.5 *lite* JSON document listing the
  installed Python distributions plus this package's own version.
* :func:`build_provenance` — an in-toto SLSA-v1.0 provenance statement
  describing how a bundle was produced, what its inputs are, and the
  SHA-256 digests of the artefacts produced.

These are intentionally small (no jsonschema dependency, no GitHub Actions
plumbing) but their JSON shape matches the upstream specs so downstream
tools can ingest them.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from biomodel_monitor import __version__ as _bmm_version


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _digest_file(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_sbom(
    *,
    component_name: str = "biomodel-monitor",
    component_version: str | None = None,
    include_distributions: bool = True,
) -> dict:
    """Build a CycloneDX-1.5-lite SBOM as a JSON-serialisable dict."""
    components: list[dict] = []
    if include_distributions:
        seen: set[str] = set()
        for dist in importlib_metadata.distributions():
            try:
                name = dist.metadata["Name"]
                version = dist.version
            except KeyError:
                continue
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name.lower()}@{version}",
            })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": _now_iso(),
            "tools": [{"vendor": "biomodel-monitor",
                       "name": "sbom-generator",
                       "version": _bmm_version}],
            "component": {
                "type": "application",
                "name": component_name,
                "version": component_version or _bmm_version,
            },
        },
        "components": components,
    }


def build_provenance(
    *,
    subject_paths: Iterable[Path | str],
    invocation: str,
    materials: Iterable[Path | str] | None = None,
    builder_id: str = "biomodel-monitor",
) -> dict:
    """Build an in-toto SLSA-v1.0 provenance statement.

    Parameters
    ----------
    subject_paths:
        The output artefacts whose digests appear under ``subject``.
    invocation:
        Human-readable description of the command that produced them
        (e.g. ``"biomodel-monitor export-bundle --out bundle/"``).
    materials:
        Input files referenced by the build (configs, baselines, etc.).
    builder_id:
        Identity of the builder; defaults to this package.
    """
    subjects = []
    for p in subject_paths:
        sp = Path(p)
        subjects.append({
            "name": sp.name,
            "digest": {"sha256": _digest_file(sp)},
        })
    mats = []
    for m in materials or []:
        mp = Path(m)
        mats.append({
            "uri": f"file://{mp.resolve()}",
            "digest": {"sha256": _digest_file(mp)} if mp.is_file() else {},
        })
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": subjects,
        "predicate": {
            "buildDefinition": {
                "buildType": "https://biomodel-monitor.dev/build/v1",
                "externalParameters": {"invocation": invocation},
                "internalParameters": {
                    "biomodel_monitor_version": _bmm_version,
                    "python_version": sys.version.split()[0],
                    "platform": sys.platform,
                },
                "resolvedDependencies": mats,
            },
            "runDetails": {
                "builder": {"id": builder_id},
                "metadata": {
                    "invocationId": os.environ.get(
                        "GITHUB_RUN_ID",
                        f"local-{int(datetime.now().timestamp())}",
                    ),
                    "startedOn": _now_iso(),
                },
            },
        },
    }


__all__ = ["build_provenance", "build_sbom"]
