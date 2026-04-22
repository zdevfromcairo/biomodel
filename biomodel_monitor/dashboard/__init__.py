"""Streamlit dashboard for BioModel Monitor.

Run with:

    streamlit run -m biomodel_monitor.dashboard.app -- --json reports_out/<batch>.json

Pages: Overview, Drift, Calibration, Subgroups, Plausibility, Alerts,
Incident Detail (retrospective).
"""

from biomodel_monitor.dashboard.app import main  # noqa: F401

__all__ = ["main"]
