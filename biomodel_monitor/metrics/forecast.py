"""Drift forecasting (v0.6).

Most monitoring tools tell you a metric *is* drifting. This module answers a
sharper question: **when, if at all, will the metric cross the alert
threshold at the current trajectory?**

The forecaster is intentionally simple — Holt's linear method (double
exponential smoothing) — for two reasons:

* It's robust on the short, noisy, irregularly-spaced metric histories you
  actually get from per-batch monitoring (vs. ARIMA et al.).
* It has zero external dependencies and is trivially reviewable by a model
  owner / auditor, which matters in regulated environments.

The output gives you, for any metric history:

* A point forecast for the next ``horizon`` steps.
* A symmetric confidence band derived from in-sample residual stdev.
* An ETA-to-threshold (in steps) and a clear severity:
    - ``alert``  — already over the alert threshold.
    - ``warn``   — projected to cross the alert threshold within the horizon.
    - ``ok``     — no projected crossing.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class ForecastPoint:
    step: int
    value: float
    lower: float
    upper: float


@dataclass
class ForecastResult:
    """Result of a forecast over a metric history."""

    metric_name: str
    history: list[float]
    forecast: list[ForecastPoint]
    threshold: float | None
    direction: Literal["above", "below"]
    eta_to_breach: int | None
    severity: Literal["ok", "warn", "alert"]
    method: str = "holt"
    notes: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["forecast"] = [asdict(p) for p in self.forecast]
        return d


def _holt_fit(
    series: list[float], *, alpha: float, beta: float,
) -> tuple[list[float], float, float]:
    """Fit Holt's linear method, return (fitted, level, trend) at the end."""
    if len(series) < 2:
        raise ValueError("Holt's method needs at least 2 observations")
    level = float(series[0])
    trend = float(series[1] - series[0])
    fitted = [level]  # one-step-ahead in-sample fit
    for t in range(1, len(series)):
        prev_level = level
        level = alpha * series[t] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        fitted.append(prev_level + (level - prev_level))  # naive 1-step
    return fitted, level, trend


def _residual_std(actual: list[float], fitted: list[float]) -> float:
    """Sample stdev of one-step-ahead residuals (with Bessel correction)."""
    if len(actual) < 2:
        return 0.0
    resids = [a - f for a, f in zip(actual, fitted, strict=False)]
    mean = sum(resids) / len(resids)
    var = sum((r - mean) ** 2 for r in resids) / max(1, len(resids) - 1)
    return math.sqrt(var)


def forecast_metric(
    history: list[float],
    *,
    metric_name: str = "metric",
    horizon: int = 10,
    threshold: float | None = None,
    direction: Literal["above", "below"] = "above",
    alpha: float = 0.4,
    beta: float = 0.2,
    z: float = 1.96,
) -> ForecastResult:
    """Project a metric series ``horizon`` steps into the future.

    Parameters
    ----------
    history:
        Observed metric values, oldest → newest.
    threshold, direction:
        If supplied, ETA-to-breach is computed against this. ``direction="above"``
        means *bad if the metric goes above the threshold*.
    alpha, beta:
        Holt smoothing parameters in [0, 1]. Defaults are deliberately
        responsive on short series.
    z:
        Multiplier for the residual stdev to construct the symmetric band.
        Default ``1.96`` ≈ 95% Gaussian.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if not 0 < alpha < 1 or not 0 < beta < 1:
        raise ValueError("alpha and beta must lie in (0, 1)")

    if len(history) < 2:
        # Degenerate — emit a flat forecast with no band, no ETA.
        last = float(history[-1]) if history else 0.0
        forecast = [
            ForecastPoint(step=i + 1, value=last, lower=last, upper=last)
            for i in range(horizon)
        ]
        sev = _severity(last, threshold, direction)
        return ForecastResult(
            metric_name=metric_name, history=list(history), forecast=forecast,
            threshold=threshold, direction=direction, eta_to_breach=None,
            severity=sev, method="naive",
            notes="insufficient history for Holt; using naive last-value",
        )

    fitted, level, trend = _holt_fit(history, alpha=alpha, beta=beta)
    sigma = _residual_std(history, fitted)

    forecast: list[ForecastPoint] = []
    eta: int | None = None
    for h in range(1, horizon + 1):
        v = level + h * trend
        # Forecast variance grows with horizon — Holt's standard expression.
        var_h = (sigma ** 2) * (1.0 + (h - 1) * (alpha ** 2) * (1 + h * beta + (h * (h - 1) * (beta ** 2)) / 6.0))
        band = z * math.sqrt(max(var_h, 0.0))
        forecast.append(ForecastPoint(step=h, value=v, lower=v - band, upper=v + band))
        if eta is None and threshold is not None and _crosses(v, threshold, direction):
            eta = h

    last_obs = float(history[-1])
    if threshold is not None and _crosses(last_obs, threshold, direction):
        sev: Literal["ok", "warn", "alert"] = "alert"
    elif eta is not None:
        sev = "warn"
    else:
        sev = "ok"

    return ForecastResult(
        metric_name=metric_name, history=list(history), forecast=forecast,
        threshold=threshold, direction=direction, eta_to_breach=eta, severity=sev,
        method="holt",
        extra={"alpha": alpha, "beta": beta, "sigma": sigma,
               "level": level, "trend": trend, "z": z},
    )


def _severity(
    value: float, threshold: float | None, direction: str,
) -> Literal["ok", "warn", "alert"]:
    if threshold is None:
        return "ok"
    return "alert" if _crosses(value, threshold, direction) else "ok"


def _crosses(value: float, threshold: float, direction: str) -> bool:
    return value >= threshold if direction == "above" else value <= threshold
