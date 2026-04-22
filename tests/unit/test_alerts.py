"""Alert thresholding and dedup tests."""

from biomodel_monitor.alerts.engine import (
    Alert,
    AlertConfig,
    AlertEngine,
    severity_score,
)


class _Bag:
    def __init__(self, **d):
        self.__dict__.update(d)
    def as_dict(self):
        return dict(self.__dict__)


def test_severity_score_monotone():
    s_ok = severity_score("ok")
    s_warn = severity_score("warn")
    s_alert = severity_score("alert")
    assert s_ok < s_warn < s_alert
    assert s_alert <= 10.0


def test_subgroup_importance_amplifies():
    base = severity_score("warn")
    boosted = severity_score("warn", subgroup_importance=2.0)
    assert boosted > base


def test_persistence_amplifies():
    one = severity_score("warn", persistence=1)
    five = severity_score("warn", persistence=5)
    assert five > one


def test_min_severity_filters_warn_only():
    eng = AlertEngine(AlertConfig(min_severity="alert"))
    drift = [_Bag(name="psi", value=0.12, severity="warn", p_value=None)]
    assert eng.from_drift(drift) == []


def test_alerts_emitted_above_threshold():
    eng = AlertEngine(AlertConfig(min_severity="warn"))
    drift = [_Bag(name="psi", value=0.30, severity="alert", p_value=None)]
    out = eng.from_drift(drift)
    assert len(out) == 1
    assert out[0].category == "drift"
    assert out[0].severity == "alert"


def test_dedup_keeps_highest_score():
    a = Alert(key="k", title="t", severity="warn", score=2.0, category="drift")
    b = Alert(key="k", title="t", severity="alert", score=8.0, category="drift")
    out = AlertEngine.deduplicate([a, b])
    assert len(out) == 1
    assert out[0].score == 8.0


def test_subgroup_importance_used_per_dimension():
    eng = AlertEngine(
        AlertConfig(min_severity="warn", importance_by_dimension={"site_id": 2.0})
    )
    sub = [
        _Bag(
            dimension="site_id", value="C", n=100, metric="accuracy",
            metric_value=0.6, global_value=0.8, delta=-0.2, ci=None,
            severity="alert", extra={},
        )
    ]
    out = eng.from_subgroups(sub)
    assert out[0].score > severity_score("alert")  # uplifted by importance
