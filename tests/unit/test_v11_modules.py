"""Unit tests for v0.11 modules: observability, modality, vector, fingerprint."""

from __future__ import annotations

import pytest

# ------------------------------------------------------------- observability

def test_otel_no_op_when_disabled(monkeypatch):
    from biomodel_monitor import observability as obs
    # Force re-init in disabled mode.
    monkeypatch.delenv("BIOMODEL_OTEL", raising=False)
    monkeypatch.setattr(obs, "_INITIALISED", False, raising=False)
    monkeypatch.setattr(obs, "_ENABLED", False, raising=False)

    enabled = obs.init_otel()
    assert enabled is False
    assert obs.is_enabled() is False

    with obs.span("noop", foo="bar") as s:
        assert s is None

    c = obs.counter("noop_counter")
    h = obs.histogram("noop_hist")
    # Should not raise.
    c.add(1, attributes={"k": "v"})
    h.record(0.1)


def test_otel_status_shape():
    from biomodel_monitor import observability as obs
    s = obs.status()
    assert "enabled" in s and "service_name" in s


def test_otel_span_propagates_exceptions(monkeypatch):
    from biomodel_monitor import observability as obs
    monkeypatch.setattr(obs, "_INITIALISED", True, raising=False)
    monkeypatch.setattr(obs, "_ENABLED", False, raising=False)
    with pytest.raises(RuntimeError):
        with obs.span("noop"):
            raise RuntimeError("boom")


# ----------------------------------------------------------------- modality

_REF_IMG = {
    "width_mean": 1024.0, "height_mean": 768.0,
    "intensity_mean": 100.0, "intensity_std": 30.0, "channels": 3,
}


def test_image_drift_ok_for_identical():
    from biomodel_monitor.modality import check_image
    res = check_image(_REF_IMG, dict(_REF_IMG))
    assert res.severity == "ok"
    assert res.value == 0.0
    assert res.kind == "image"
    assert "diffs" in res.extra


def test_image_drift_alerts_on_channel_change():
    from biomodel_monitor.modality import check_image
    cur = dict(_REF_IMG, channels=1)
    res = check_image(_REF_IMG, cur)
    assert res.severity == "alert"
    assert res.extra["channel_changed"] is True


def test_image_drift_warns_on_intensity_shift():
    from biomodel_monitor.modality import check_image
    cur = dict(_REF_IMG, intensity_mean=120.0)  # 20% rel diff
    res = check_image(_REF_IMG, cur, warn=0.10, alert=0.30)
    assert res.severity == "warn"


def test_image_missing_keys_raises():
    from biomodel_monitor.modality import check_image
    bad = {"width_mean": 1.0}
    with pytest.raises(ValueError):
        check_image(bad, _REF_IMG)


def test_text_drift_uses_vocab_overlap():
    from biomodel_monitor.modality import check_text
    ref = {
        "token_length_mean": 18.0, "token_length_std": 5.0,
        "type_token_ratio": 0.3, "vocab_size": 5000,
        "top_tokens": ["cancer", "lesion", "biopsy", "lung", "patient"],
    }
    cur = dict(ref, top_tokens=["cancer", "lesion", "stroke", "covid",
                                "ventilator"])
    res = check_text(ref, cur, warn=0.30, alert=0.80)
    # 2/8 overlap → 0.75 vocab_overlap_loss → alert under our bands? 0.75<0.80
    assert res.severity == "warn"
    assert res.extra["worst_feature"] == "vocab_overlap_loss"


def test_text_drift_ok_when_unchanged():
    from biomodel_monitor.modality import check_text
    ref = {
        "token_length_mean": 18.0, "token_length_std": 5.0,
        "type_token_ratio": 0.3, "vocab_size": 5000,
    }
    res = check_text(ref, dict(ref))
    assert res.severity == "ok"


def test_tabular_drift_finds_worst_column():
    from biomodel_monitor.modality import check_tabular
    ref = {"missingness": {"age": 0.01, "bmi": 0.02, "tumor_stage": 0.05}}
    cur = {"missingness": {"age": 0.01, "bmi": 0.02, "tumor_stage": 0.40}}
    res = check_tabular(ref, cur)
    assert res.severity == "alert"
    assert res.extra["worst_column"] == "tumor_stage"


def test_dispatch_unknown_kind_raises():
    from biomodel_monitor.modality import check_modality
    with pytest.raises(ValueError):
        check_modality("audio", {}, {})


# ------------------------------------------------------------------ vector

def test_vector_store_add_and_query(tmp_path):
    from biomodel_monitor.vector import VectorStore
    vs = VectorStore(tmp_path / "v.db")
    vs.add("ns1", "r1", [1.0, 0.0, 0.0], metadata={"label": "A"})
    vs.add("ns1", "r2", [0.9, 0.1, 0.0], metadata={"label": "A"})
    vs.add("ns1", "r3", [0.0, 1.0, 0.0], metadata={"label": "B"})
    res = vs.query("ns1", [1.0, 0.0, 0.0], k=2)
    assert [n.record_id for n in res] == ["r1", "r2"]
    assert res[0].distance == pytest.approx(0.0, abs=1e-9)
    assert res[0].metadata == {"label": "A"}
    vs.close()


def test_vector_store_explain_envelope(tmp_path):
    from biomodel_monitor.vector import VectorStore
    vs = VectorStore(tmp_path / "v.db")
    vs.add("ns", "a", [1.0, 0.0])
    vs.add("ns", "b", [0.0, 1.0])
    out = vs.explain("ns", [1.0, 0.0], k=1)
    assert out["n_total"] == 2
    assert out["k"] == 1
    assert out["neighbors"][0]["record_id"] == "a"
    vs.close()


def test_vector_store_dim_mismatch_skipped(tmp_path):
    from biomodel_monitor.vector import VectorStore
    vs = VectorStore(tmp_path / "v.db")
    vs.add("ns", "good", [1.0, 0.0, 0.0])
    vs.add("ns", "old", [1.0, 0.0])  # different dim, e.g. previous embedding
    res = vs.query("ns", [1.0, 0.0, 0.0], k=5)
    assert [n.record_id for n in res] == ["good"]
    vs.close()


def test_vector_store_rejects_bad_inputs(tmp_path):
    from biomodel_monitor.vector import VectorStore
    vs = VectorStore(tmp_path / "v.db")
    with pytest.raises(ValueError):
        vs.add("ns", "r", [])
    with pytest.raises(ValueError):
        vs.add("ns", "r", [float("nan")])
    with pytest.raises(ValueError):
        vs.query("ns", [1.0], k=0)
    vs.close()


# --------------------------------------------------------------- fingerprint

def test_fingerprint_deterministic():
    from biomodel_monitor.fingerprint import compute_fingerprint
    p = [[0.1, 0.9], [0.4, 0.6]]
    f1 = compute_fingerprint("canary-v1", p)
    f2 = compute_fingerprint("canary-v1", p)
    assert f1.fingerprint == f2.fingerprint
    assert f1.fingerprint.startswith("sha256:")
    assert f1.n_predictions == 2


def test_fingerprint_changes_on_prediction_change():
    from biomodel_monitor.fingerprint import compute_fingerprint
    f1 = compute_fingerprint("canary", [[0.1, 0.9]])
    f2 = compute_fingerprint("canary", [[0.2, 0.8]])
    assert f1.fingerprint != f2.fingerprint


def test_fingerprint_quantisation_is_lenient():
    from biomodel_monitor.fingerprint import compute_fingerprint
    # Floating-point noise below quantisation precision should not change.
    f1 = compute_fingerprint("canary", [[0.123456, 0.876544]])
    f2 = compute_fingerprint("canary", [[0.1234564, 0.8765442]])
    assert f1.fingerprint == f2.fingerprint


def test_fingerprint_compare_match_and_mismatch():
    from biomodel_monitor.fingerprint import (
        compare_fingerprints,
        compute_fingerprint,
    )
    f = compute_fingerprint("c", [[1.0, 0.0]])
    same = compare_fingerprints(f.fingerprint, f.fingerprint)
    assert same["matches"] is True
    assert same["severity"] == "ok"

    diff = compare_fingerprints(f.fingerprint, "sha256:" + "0" * 64)
    assert diff["matches"] is False
    assert diff["severity"] == "alert"


def test_fingerprint_rejects_invalid_inputs():
    from biomodel_monitor.fingerprint import compute_fingerprint
    with pytest.raises(ValueError):
        compute_fingerprint("", [[1.0]])
    with pytest.raises(ValueError):
        compute_fingerprint("c", [])
    with pytest.raises(ValueError):
        compute_fingerprint("c", [[float("inf")]])
