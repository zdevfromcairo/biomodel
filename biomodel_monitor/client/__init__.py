"""Python SDK for the BioModel Monitor HTTP API (v0.6).

A thin, well-typed wrapper around the v0.4 server's REST endpoints. Built on
``urllib`` so the SDK has **zero** runtime dependencies — installing the
core package is enough to use it, even on locked-down clinical terminals.

Example:

    from biomodel_monitor.client import BioModelMonitorClient
    c = BioModelMonitorClient("https://monitor.example.com", api_key="…")
    c.health()
    c.list_alerts(model_id="pathology-tumor-clf")
    c.ingest("pathology-tumor-clf", "1.0.0", [record_dict, record_dict])
"""

from biomodel_monitor.client.client import (
    BioModelMonitorClient,
    HTTPError,
)

__all__ = ["BioModelMonitorClient", "HTTPError"]
