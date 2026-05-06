"""OpenTelemetry integration (v0.11) — optional, no-op fallback.

The pipeline is now instrumented with OpenTelemetry spans so any operator
running with an OTEL collector gets distributed traces and metrics for free.

The whole module is best-effort: if ``opentelemetry-api`` is not installed,
the helpers degrade to no-ops and code paths that import them keep working.
This is the same opt-in philosophy as v0.7 (multi-tenancy) and v0.9
(registry / policy).

Configuration is via standard env vars:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — collector URL
* ``OTEL_SERVICE_NAME``           — defaults to ``biomodel-monitor``
* ``BIOMODEL_OTEL`` ∈ {``0``, ``1``} — master switch (default: off)

We only depend on the *API* package (``opentelemetry-api``); SDK +
exporters are pulled in by the operator's own deployment.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

_TRACER = None
_METER = None
_INITIALISED = False
_ENABLED = False


def is_enabled() -> bool:
    """Return ``True`` once :func:`init_otel` has successfully wired up."""
    return _ENABLED


def init_otel(*, service_name: str | None = None,
              force: bool = False) -> bool:
    """Wire up OpenTelemetry. Returns ``True`` if active, ``False`` otherwise.

    Safe to call repeatedly — only initialises once. If the API package is
    missing or ``BIOMODEL_OTEL`` is not truthy, this is a no-op and returns
    ``False`` (callers can then proceed unchanged).
    """
    global _TRACER, _METER, _INITIALISED, _ENABLED
    if _INITIALISED and not force:
        return _ENABLED

    flag = (os.environ.get("BIOMODEL_OTEL") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on") and not force:
        _INITIALISED = True
        _ENABLED = False
        return False

    try:
        from opentelemetry import metrics, trace  # type: ignore[import-not-found]
    except ImportError:
        _INITIALISED = True
        _ENABLED = False
        return False

    name = service_name or os.environ.get("OTEL_SERVICE_NAME",
                                          "biomodel-monitor")
    _TRACER = trace.get_tracer(name)
    _METER = metrics.get_meter(name)
    _INITIALISED = True
    _ENABLED = True
    return True


@contextmanager
def span(name: str, **attributes: Any):
    """Context manager that opens a span when OTEL is enabled, else no-ops.

    Use it freely in pipeline code — the cost when disabled is one dict
    lookup and one ``yield``.
    """
    if not _ENABLED or _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as s:  # type: ignore[union-attr]
        for k, v in attributes.items():
            try:
                s.set_attribute(k, v)
            except Exception:  # noqa: BLE001 — never let telemetry crash callers
                pass
        try:
            yield s
        except Exception as exc:
            try:
                s.record_exception(exc)
            except Exception:  # noqa: BLE001
                pass
            raise


def counter(name: str, *, unit: str = "1", description: str = ""):
    """Return a counter instrument, or a no-op object if OTEL is disabled."""
    if not _ENABLED or _METER is None:
        class _Noop:
            def add(self, *_args, **_kwargs) -> None:  # noqa: D401
                """No-op."""
        return _Noop()
    return _METER.create_counter(name, unit=unit, description=description)


def histogram(name: str, *, unit: str = "1", description: str = ""):
    """Return a histogram instrument, or a no-op object if OTEL is disabled."""
    if not _ENABLED or _METER is None:
        class _Noop:
            def record(self, *_args, **_kwargs) -> None:  # noqa: D401
                """No-op."""
        return _Noop()
    return _METER.create_histogram(name, unit=unit, description=description)


def status() -> dict[str, Any]:
    """Inspect runtime status — handy for a ``/otel/status`` endpoint."""
    return {
        "enabled": _ENABLED,
        "initialised": _INITIALISED,
        "endpoint": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        "service_name": os.environ.get("OTEL_SERVICE_NAME",
                                       "biomodel-monitor"),
    }


__all__ = [
    "counter",
    "histogram",
    "init_otel",
    "is_enabled",
    "span",
    "status",
]
