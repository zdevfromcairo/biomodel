"""Rolling baseline learner with explicit promotion gate.

A baseline starts as a *candidate* in the metrics store, accumulates samples
across multiple batches via :meth:`update`, and is only swapped in for drift
detection once a human (or an automated check) calls :func:`promote_candidate`.
This is intentional — auto-promoting baselines is how silent failures get
laundered into "the new normal" in production medical AI systems.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict

from biomodel_monitor.baselines.store import Baseline
from biomodel_monitor.schema.models import PredictionBatch, PredictionRecord
from biomodel_monitor.store.repository import MetricsStore


class RollingBaselineLearner:
    """Append-only baseline learner with a configurable max window."""

    def __init__(
        self,
        *,
        store: MetricsStore,
        model_id: str,
        model_version: str,
        cohort: str | None = None,
        site_id: str | None = None,
        max_window: int = 5000,
    ) -> None:
        if max_window <= 0:
            raise ValueError("max_window must be positive")
        self.store = store
        self.model_id = model_id
        self.model_version = model_version
        self.cohort = cohort
        self.site_id = site_id
        self.max_window = max_window

    # ----- helpers -----------------------------------------------------------
    def _load_candidate(self) -> tuple[int | None, dict | None]:
        cands = [
            b for b in self.store.list_baselines(
                model_id=self.model_id,
                model_version=self.model_version,
                status="candidate",
            )
            if (b.get("cohort") or None) == self.cohort
            and (b.get("site_id") or None) == self.site_id
        ]
        if not cands:
            return None, None
        bid = cands[0]["id"]
        # fetch full payload
        full = self.store._tx  # noqa: SLF001
        with full() as c:
            row = c.execute(
                "SELECT payload_json FROM baselines WHERE id=?", (bid,)
            ).fetchone()
        import json as _json
        return bid, _json.loads(row["payload_json"])

    def _select_records(self, batch: PredictionBatch) -> list[PredictionRecord]:
        recs = batch.records
        if self.cohort is not None:
            recs = [r for r in recs if r.cohort == self.cohort]
        if self.site_id is not None:
            recs = [r for r in recs if r.site_id == self.site_id]
        return recs

    @staticmethod
    def _trim(seq: list, cap: int) -> list:
        return seq[-cap:] if len(seq) > cap else seq

    # ----- API ---------------------------------------------------------------
    def update(self, batch: PredictionBatch) -> dict:
        """Fold a batch into the candidate baseline and return its current payload."""
        recs = self._select_records(batch)
        if not recs:
            raise ValueError("no records match the learner's cohort/site filter")

        bid, payload = self._load_candidate()
        if payload is None:
            payload = asdict(
                Baseline.from_batch(batch, cohort=self.cohort)
            )
            payload["site_id"] = self.site_id
        # extend
        new_scores = [r.score for r in recs]
        new_sites = [r.site_id for r in recs]
        new_scanners = [r.scanner_id or "" for r in recs]
        new_stains = [r.stain or "" for r in recs]
        new_tissues = [r.tissue_type or "" for r in recs]
        payload["scores"] = self._trim(payload.get("scores", []) + new_scores, self.max_window)
        payload["sites"] = self._trim(payload.get("sites", []) + new_sites, self.max_window)
        payload["scanners"] = self._trim(payload.get("scanners", []) + new_scanners, self.max_window)
        payload["stains"] = self._trim(payload.get("stains", []) + new_stains, self.max_window)
        payload["tissue_types"] = self._trim(payload.get("tissue_types", []) + new_tissues, self.max_window)
        feats: dict[str, list[float]] = payload.get("features", {})
        for r in recs:
            if not r.features:
                continue
            for k, v in r.features.items():
                feats.setdefault(k, []).append(float(v))
        for k in list(feats.keys()):
            feats[k] = self._trim(feats[k], self.max_window)
        payload["features"] = feats
        payload["n"] = len(payload["scores"])
        payload["model_id"] = self.model_id
        payload["model_version"] = self.model_version
        payload["cohort"] = self.cohort

        # retire the old candidate row, write a fresh one (cheaper than UPDATE blob diff)
        if bid is not None:
            with self.store._tx() as c:  # noqa: SLF001
                c.execute("UPDATE baselines SET status='retired' WHERE id=?", (bid,))
        self.store.save_baseline(
            model_id=self.model_id, model_version=self.model_version,
            cohort=self.cohort, site_id=self.site_id,
            status="candidate", payload=payload,
        )
        return payload

    def candidate(self) -> Baseline | None:
        _, payload = self._load_candidate()
        if payload is None:
            return None
        # only the canonical Baseline fields
        keep = {"model_id", "model_version", "cohort", "n", "scores", "features",
                "sites", "scanners", "stains", "tissue_types"}
        return Baseline(**{k: payload.get(k) for k in keep})  # type: ignore[arg-type]


def promote_candidate(
    *,
    store: MetricsStore,
    model_id: str,
    model_version: str,
    cohort: str | None = None,
    site_id: str | None = None,
    min_n: int = 200,
) -> int:
    """Promote the most recent candidate baseline matching this scope.

    Raises if no candidate exists or it has fewer than ``min_n`` samples.
    Returns the promoted baseline id.
    """
    cands: Iterable[dict] = store.list_baselines(
        model_id=model_id, model_version=model_version, status="candidate"
    )
    cands = [
        b for b in cands
        if (b.get("cohort") or None) == cohort
        and (b.get("site_id") or None) == site_id
    ]
    if not cands:
        raise ValueError("no candidate baseline to promote")
    target = cands[0]
    if int(target["n"]) < min_n:
        raise ValueError(
            f"candidate baseline has only {target['n']} samples; needs at least {min_n}"
        )
    store.promote_baseline(int(target["id"]))
    return int(target["id"])
