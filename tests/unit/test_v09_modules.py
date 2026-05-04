"""Unit tests for v0.9 modules: registry, policy, dp, canary, sbom, wasserstein."""

from __future__ import annotations

import math

import numpy as np
import pytest

# ---------------------------------------------------------------- registry --

def test_registry_register_and_lineage(tmp_path):
    from biomodel_monitor.registry import LineageEdge, ModelRecord, ModelRegistry

    reg = ModelRegistry(tmp_path / "reg.db")
    reg.register(ModelRecord(model_id="m1", model_version="1",
                             training_data_hash="abc", framework="pytorch"))
    reg.register(ModelRecord(model_id="m2", model_version="1",
                             training_data_hash="def"))
    assert reg.get("m1", "1").framework == "pytorch"
    assert {r.model_id for r in reg.list()} == {"m1", "m2"}

    reg.add_edge(LineageEdge(
        upstream_model_id="m1", upstream_model_version="1",
        downstream_model_id="m2", downstream_model_version="1",
    ))
    ups = reg.upstreams("m2", "1")
    downs = reg.downstreams("m1", "1")
    assert len(ups) == 1 and ups[0].upstream_model_id == "m1"
    assert len(downs) == 1 and downs[0].downstream_model_id == "m2"
    reg.close()


def test_registry_quarantine_and_assert(tmp_path):
    from biomodel_monitor.registry import (
        ModelRecord,
        ModelRegistry,
        QuarantineError,
    )

    reg = ModelRegistry(tmp_path / "reg.db")
    reg.register(ModelRecord(model_id="m", model_version="1"))

    # not quarantined → no-op
    reg.assert_active("m", "1")
    # unregistered → no-op (backwards compatibility)
    reg.assert_active("nonexistent", "0")

    rec = reg.quarantine("m", "1", note="ECE alert persisted 3+")
    assert rec.status == "quarantined"
    assert rec.notes == "ECE alert persisted 3+"
    with pytest.raises(QuarantineError):
        reg.assert_active("m", "1")

    reg.unquarantine("m", "1")
    reg.assert_active("m", "1")  # back to fine

    with pytest.raises(KeyError):
        reg.quarantine("ghost", "1")
    reg.close()


# --------------------------------------------------------------- policy ----

def test_policy_engine_matches_severity_and_persistence():
    from biomodel_monitor.policy import Policy, PolicyEngine, PolicyMatch

    eng = PolicyEngine([
        Policy(
            name="quarantine-on-persistent-ece",
            when=PolicyMatch(metric="ece", severity_at_least="alert",
                             persistence_at_least=3),
            action="quarantine",
            message="quarantine please",
        ),
        Policy(
            name="notify-on-mmd-warn",
            when=PolicyMatch(metric="mmd_rbf", severity_at_least="warn"),
            action="notify",
        ),
    ])
    alerts = [
        {"key": "a1", "severity": "alert", "persistence": 3,
         "category": "calibration", "details": {"name": "ece"}},
        {"key": "a2", "severity": "warn", "persistence": 1,
         "category": "drift", "details": {"name": "mmd_rbf"}},
        {"key": "a3", "severity": "warn", "persistence": 2,
         "category": "calibration", "details": {"name": "ece"}},
    ]
    actions = eng.evaluate(alerts, model_id="m", model_version="1")
    by_alert = {(a.alert_key, a.action) for a in actions}
    assert ("a1", "quarantine") in by_alert
    assert ("a2", "notify") in by_alert
    # a3 fails persistence + severity threshold
    assert ("a3", "quarantine") not in by_alert
    assert ("a3", "notify") not in by_alert


def test_policy_engine_from_yaml_unknown_action_rejected():
    import yaml

    from biomodel_monitor.policy import PolicyEngine

    text = yaml.safe_dump({
        "policies": [
            {"name": "nope", "when": {"metric": "x"}, "action": "explode"},
        ],
    })
    with pytest.raises(ValueError):
        PolicyEngine.from_yaml(text)


# ------------------------------------------------------------------- dp ----

def test_dp_accountant_blocks_overspend():
    from biomodel_monitor.federated.dp import PrivacyAccountant

    acc = PrivacyAccountant(budget_epsilon=1.0)
    acc.spend(0.4, label="hist")
    acc.spend(0.5, label="mean")
    assert math.isclose(acc.spent, 0.9)
    with pytest.raises(ValueError):
        acc.spend(0.5)


def test_dp_laplace_noise_unbiased_with_seed_deterministic():
    from biomodel_monitor.federated.dp import laplace_noise

    a = laplace_noise(np.zeros(2000), sensitivity=1.0, epsilon=1.0, seed=7)
    b = laplace_noise(np.zeros(2000), sensitivity=1.0, epsilon=1.0, seed=7)
    np.testing.assert_array_equal(a, b)
    # mean of Laplace(0, 1) over 2000 samples should be near zero
    assert abs(np.mean(a)) < 0.1


def test_dp_privatise_histogram_nonnegative():
    from biomodel_monitor.federated.dp import (
        PrivacyAccountant,
        privatise_histogram,
    )

    acc = PrivacyAccountant(budget_epsilon=2.0)
    out = privatise_histogram([10, 20, 30, 40], epsilon=1.0,
                              accountant=acc, seed=0)
    assert (out >= 0).all()
    assert acc.remaining() == pytest.approx(1.0)


# --------------------------------------------------------------- canary ----

def test_canary_promote_when_canary_better():
    from biomodel_monitor.canary import CanaryMonitor

    rng = np.random.default_rng(0)
    mon = CanaryMonitor(alpha=0.01, tau=0.1, min_n=30)
    for x in rng.binomial(1, 0.7, 200):
        mon.add_control(x)
    for x in rng.binomial(1, 0.85, 200):
        mon.add_canary(x)
    d = mon.decide()
    assert d.verdict in ("promote", "inconclusive")
    assert d.diff_mean > 0


def test_canary_rollback_when_canary_worse():
    from biomodel_monitor.canary import CanaryMonitor

    rng = np.random.default_rng(1)
    mon = CanaryMonitor(alpha=0.01, tau=0.1, min_n=30)
    for x in rng.binomial(1, 0.85, 400):
        mon.add_control(x)
    for x in rng.binomial(1, 0.55, 400):
        mon.add_canary(x)
    d = mon.decide()
    assert d.verdict == "rollback"
    assert d.severity == "alert"
    assert d.diff_mean < 0


def test_canary_inconclusive_below_min_n():
    from biomodel_monitor.canary import CanaryMonitor

    mon = CanaryMonitor(alpha=0.01, min_n=50)
    for _ in range(10):
        mon.add_control(0.5)
        mon.add_canary(0.4)
    assert mon.decide().verdict == "inconclusive"


# ---------------------------------------------------------------- sbom -----

def test_sbom_has_required_fields():
    from biomodel_monitor.security import build_sbom

    sbom = build_sbom()
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["name"] == "biomodel-monitor"
    # numpy is a hard dep, must appear
    names = {c["name"].lower() for c in sbom["components"]}
    assert "numpy" in names


def test_provenance_subjects_have_sha256(tmp_path):
    from biomodel_monitor.security import build_provenance

    f = tmp_path / "bundle.zip"
    f.write_bytes(b"hello")
    p = build_provenance(
        subject_paths=[f],
        invocation="biomodel-monitor export-bundle --out bundle.zip",
    )
    assert p["predicateType"].startswith("https://slsa.dev/provenance/")
    assert p["subject"][0]["digest"]["sha256"]
    assert len(p["subject"][0]["digest"]["sha256"]) == 64


# ----------------------------------------------------- wasserstein -----

def test_wasserstein_1d_zero_for_identical():
    from biomodel_monitor.metrics.wasserstein import sliced_wasserstein

    rng = np.random.default_rng(2)
    x = rng.normal(0, 1, 500)
    res = sliced_wasserstein(x, x.copy())
    assert res.value == pytest.approx(0.0, abs=1e-9)
    assert res.severity == "ok"
    assert res.n_projections == 1  # 1-D path


def test_wasserstein_2d_alerts_on_shift():
    from biomodel_monitor.metrics.wasserstein import sliced_wasserstein

    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1, (300, 4))
    cur = rng.normal(0.6, 1, (300, 4))
    res = sliced_wasserstein(ref, cur, n_projections=32, seed=0)
    assert res.n_projections == 32
    assert res.value > 0
    assert res.severity in ("warn", "alert")


def test_wasserstein_dimension_mismatch_raises():
    from biomodel_monitor.metrics.wasserstein import sliced_wasserstein

    with pytest.raises(ValueError):
        sliced_wasserstein(np.zeros((10, 2)), np.zeros((10, 3)))
