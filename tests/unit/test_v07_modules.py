"""Tests for v0.7: tenancy, plugins, federated, audit, uncertainty."""

from __future__ import annotations

import json

import numpy as np
import pytest

from biomodel_monitor.audit import AuditLog
from biomodel_monitor.federated import (
    CalibrationSummary,
    HistogramSummary,
    MomentSummary,
    aggregate_calibration,
    aggregate_histograms,
    aggregate_moments,
)
from biomodel_monitor.metrics.uncertainty import (
    mutual_information,
    predictive_entropy,
    summarise_uncertainty,
)
from biomodel_monitor.plugins import PluginRegistry, discover_plugins
from biomodel_monitor.tenancy import (
    AccessDenied,
    TenantContext,
    TenantRegistry,
    role_satisfies,
)

# --------------------------------------------------------------------- tenancy


def test_role_satisfies_orders_correctly():
    assert role_satisfies("admin", "viewer")
    assert role_satisfies("writer", "operator")
    assert not role_satisfies("viewer", "operator")
    assert role_satisfies("viewer", "viewer")


def test_tenant_context_require_grants_and_denies():
    viewer = TenantContext(tenant_id="t1", role="viewer")
    viewer.require("list_alerts")          # grant
    with pytest.raises(AccessDenied):
        viewer.require("annotate")          # deny
    with pytest.raises(AccessDenied):
        viewer.require("manage_tenants")    # deny
    assert viewer.can("list_alerts")
    assert not viewer.can("annotate")


def test_tenant_registry_lookup_and_fingerprint():
    reg = TenantRegistry.from_mapping({
        "key-aaaaaaa1": ("tenantA", "writer"),
        "key-bbbbbbb2": ("tenantB", "viewer"),
    })
    tc = reg.lookup("key-aaaaaaa1")
    assert tc and tc.tenant_id == "tenantA" and tc.role == "writer"
    assert tc.api_key_id == "aaa1"  # last 4
    assert reg.lookup(None) is None
    assert reg.lookup("missing") is None
    assert sorted(reg.tenants()) == ["tenantA", "tenantB"]


def test_tenant_registry_from_env_parses_and_validates():
    reg = TenantRegistry.from_env({
        "BIOMODEL_TENANT_KEYS": "k1:tA:writer, k2:tB:viewer",
    })
    assert len(reg) == 2
    assert reg.lookup("k1").role == "writer"  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        TenantRegistry.from_env({"BIOMODEL_TENANT_KEYS": "bad-format"})
    with pytest.raises(ValueError):
        TenantRegistry.from_env({"BIOMODEL_TENANT_KEYS": "k:tA:wizard"})


# --------------------------------------------------------------------- plugins


def test_plugin_registry_register_and_lookup():
    reg = PluginRegistry()
    reg.register("my_metric", "biomodel_monitor.metrics", lambda x: x + 1)
    p = reg.get("biomodel_monitor.metrics", "my_metric")
    assert p.callable(1) == 2
    assert p.source == "manual"
    assert len(reg) == 1
    with pytest.raises(KeyError):
        reg.get("biomodel_monitor.metrics", "nope")
    with pytest.raises(ValueError):
        reg.register("my_metric", "biomodel_monitor.metrics", lambda x: x)
    with pytest.raises(ValueError):
        reg.register("x", "wrong-group", lambda x: x)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        reg.register("y", "biomodel_monitor.metrics", "not callable")  # type: ignore[arg-type]


def test_plugin_discover_handles_no_entry_points_gracefully():
    # We have no plugins installed in the test env. discover should be a no-op.
    reg = discover_plugins()
    assert isinstance(reg, PluginRegistry)


# --------------------------------------------------------------------- federated


def _hist(site, counts):
    return HistogramSummary(site_id=site, bin_edges=[0, 0.5, 1.0], counts=counts)


def test_aggregate_histograms_pools_and_flags_outlier():
    a = _hist("A", [80, 20])
    b = _hist("B", [60, 40])
    c = _hist("C", [10, 90])  # very different
    out = aggregate_histograms([a, b, c], warn_psi=0.10, alert_psi=0.30)
    assert out.pooled_n == 300
    assert out.severity == "alert"
    assert out.per_site_psi["C"] > out.per_site_psi["A"]
    assert sum(out.pooled_distribution) == pytest.approx(1.0)


def test_aggregate_histograms_rejects_misaligned_bins():
    a = HistogramSummary("A", [0, 0.5, 1.0], [50, 50])
    b = HistogramSummary("B", [0, 0.3, 1.0], [50, 50])
    with pytest.raises(ValueError):
        aggregate_histograms([a, b])


def test_aggregate_calibration_pools_ece():
    edges = [0.0, 0.5, 1.0]
    a = CalibrationSummary("A", edges,
                           bin_count=[50, 50], bin_pos=[10, 45], bin_conf_sum=[10, 40])
    b = CalibrationSummary("B", edges,
                           bin_count=[50, 50], bin_pos=[5, 48], bin_conf_sum=[12, 42])
    out = aggregate_calibration([a, b], warn_ece=0.01, alert_ece=0.05)
    assert out.pooled_n == 200
    assert out.pooled_ece >= 0
    assert set(out.per_site_ece.keys()) == {"A", "B"}


def test_aggregate_moments_flags_outlier_site():
    # 9 normal-ish sites + 1 outlier so the pooled stdev stays small.
    sites = [MomentSummary(f"S{i}", n=100, sum=50.0, sum_sq=30.0) for i in range(9)]
    sites.append(MomentSummary("OUT", n=100, sum=95.0, sum_sq=95.0))
    out = aggregate_moments(sites, warn_z=1.0, alert_z=2.0)
    assert out.pooled_n == 1000
    assert out.per_site_z["OUT"] > out.per_site_z["S0"]
    assert out.severity in {"warn", "alert"}


def test_aggregate_moments_validates_inputs():
    with pytest.raises(ValueError):
        aggregate_moments([])


# --------------------------------------------------------------------- audit


def test_audit_log_appends_and_chains(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    e1 = log.append("annotate", {"key": "K1"}, actor_tenant="t1", actor_role="operator")
    e2 = log.append("promote_baseline", {"baseline_id": 7},
                    actor_tenant="t1", actor_role="writer")
    assert e1.seq == 0 and e2.seq == 1
    assert e2.prev_hash == e1.entry_hash
    ok, bad = log.verify()
    assert ok and bad is None


def test_audit_log_detects_tampering(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = AuditLog(p)
    log.append("annotate", {"key": "K1"})
    log.append("annotate", {"key": "K2"})
    # Tamper with the first line's payload.
    lines = p.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["payload"]["key"] = "EVIL"
    lines[0] = json.dumps(obj)
    p.write_text("\n".join(lines) + "\n")
    log2 = AuditLog(p)
    ok, bad = log2.verify()
    assert not ok
    assert bad == 0


def test_audit_log_resumes_from_existing_file(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditLog(p)
    a.append("annotate", {"k": 1})
    b = AuditLog(p)  # re-open
    e = b.append("annotate", {"k": 2})
    assert e.seq == 1
    ok, bad = b.verify()
    assert ok and bad is None


# ------------------------------------------------------------------ uncertainty


def test_predictive_entropy_zero_for_one_hot_high_for_uniform():
    one_hot = np.array([[1.0, 0.0, 0.0]])
    uniform = np.array([[1 / 3] * 3])
    h_oh = predictive_entropy(one_hot)[0]
    h_uni = predictive_entropy(uniform)[0]
    assert h_oh < 1e-6
    assert h_uni > 1.0  # log(3) ≈ 1.0986


def test_summarise_uncertainty_flags_high_entropy_batches():
    # 60% near-uniform → above-threshold
    n_near = np.full((60, 4), 0.25)
    n_low = np.tile([0.97, 0.01, 0.01, 0.01], (40, 1))
    probs = np.vstack([n_near, n_low])
    out = summarise_uncertainty(probs, warn_high_rate=0.2, alert_high_rate=0.4)
    assert out.severity == "alert"
    assert out.high_uncertainty_rate >= 0.4
    assert out.mean_mutual_information is None


def test_summarise_uncertainty_with_ensemble_emits_mi():
    rng = np.random.default_rng(0)
    members = rng.dirichlet([2, 2, 2], size=(3, 20))  # (M=3, n=20, C=3)
    probs = members.mean(axis=0)
    out = summarise_uncertainty(probs, probs_per_member=members)
    assert out.mean_mutual_information is not None
    assert out.mean_mutual_information >= 0


def test_mutual_information_validates_shape():
    with pytest.raises(ValueError):
        mutual_information(np.zeros((10, 3)))  # 2-D, not 3-D
