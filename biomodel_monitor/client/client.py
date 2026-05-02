"""Stdlib-only HTTP client for the BioModel Monitor API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


class HTTPError(RuntimeError):
    """Raised when the server returns a non-2xx status code."""

    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.body = body
        self.url = url


# A transport is anything that takes (method, url, headers, body) and returns
# (status, body_bytes). Injectable so tests don't need a network.
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _default_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None,
    *, timeout: float = 30.0,
) -> tuple[int, bytes]:  # pragma: no cover — exercised against a live server
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — caller-controlled URL
            return resp.status, resp.read()
    except _UrllibHTTPError as e:
        return e.code, e.read() or b""


class BioModelMonitorClient:
    """Synchronous client for the BioModel Monitor HTTP API.

    Parameters
    ----------
    base_url:
        Server base URL (no trailing path required).
    api_key:
        Sent as ``X-API-Key``. Pass ``None`` if the server is in
        ``--no-auth`` mode.
    transport:
        Optional injection point for tests; signature matches
        :data:`Transport`. Defaults to a stdlib ``urllib`` transport.
    timeout:
        Per-request timeout in seconds (default transport only).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: Transport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout = float(timeout)
        if transport is None:
            self._transport: Transport = lambda m, u, h, b: _default_transport(
                m, u, h, b, timeout=self.timeout,
            )
        else:
            self._transport = transport

    # ------------------------------------------------------------------ raw
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        if params:
            # Drop None values — they'd serialise to "None".
            qp = {k: v for k, v in params.items() if v is not None}
            if qp:
                url = url + ("&" if "?" in url else "?") + urlencode(qp, doseq=True)
        headers = {"Accept": "application/json", "User-Agent": "biomodel-monitor-sdk/0.8"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, url, headers, body)
        if not 200 <= status < 300:
            raise HTTPError(status, raw.decode("utf-8", errors="replace"), url)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    # ----------------------------------------------------------- endpoints
    def health(self) -> dict:
        return self.request("GET", "/health")

    def prometheus(self) -> str:
        return self.request("GET", "/metrics")

    def list_runs(
        self, *, model_id: str | None = None, model_version: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self.request("GET", "/runs", params={
            "model_id": model_id, "model_version": model_version, "limit": limit,
        })

    def list_alerts(
        self, *, run_id: str | None = None, model_id: str | None = None,
        model_version: str | None = None, key: str | None = None, limit: int = 200,
    ) -> list[dict]:
        return self.request("GET", "/alerts", params={
            "run_id": run_id, "model_id": model_id, "model_version": model_version,
            "key": key, "limit": limit,
        })

    def annotate(
        self, key: str, *, kind: str, actor: str, comment: str | None = None,
        label: str | None = None,
    ) -> dict:
        return self.request("POST", f"/alerts/{key}/annotations", json_body={
            "kind": kind, "actor": actor, "comment": comment, "label": label,
        })

    def list_baselines(self) -> list[dict]:
        return self.request("GET", "/baselines")

    def promote_baseline(self, baseline_id: int) -> dict:
        return self.request("POST", f"/baselines/{baseline_id}/promote")

    def changepoints(
        self, metric: str, *, model_id: str, model_version: str,
    ) -> dict:
        return self.request("GET", f"/changepoints/{metric}", params={
            "model_id": model_id, "model_version": model_version,
        })

    def whatif(
        self, batch_path: str, *, baseline_path: str | None = None,
        exclude: dict[str, str] | None = None,
    ) -> dict:
        return self.request("POST", "/whatif", json_body={
            "batch_path": batch_path, "baseline_path": baseline_path,
            "exclude": exclude or {},
        })

    def model_card(self, *, model_id: str, model_version: str) -> str:
        return self.request("GET", "/model-card", params={
            "model_id": model_id, "model_version": model_version,
        })

    # ----------------------------------------------------------- streaming
    def ingest(
        self,
        model_id: str,
        model_version: str,
        records: list[dict],
        *,
        flush: bool = False,
    ) -> dict:
        """Push records onto the server-side streaming buffer (v0.6).

        Set ``flush=True`` to force the server to flush this key's window
        immediately after appending (useful at end-of-day).
        """
        return self.request("POST", "/ingest", json_body={
            "model_id": model_id, "model_version": model_version,
            "records": records, "flush": bool(flush),
        })

    def forecast(
        self, *, model_id: str, model_version: str, metric: str,
        horizon: int = 10, threshold: float | None = None,
        direction: str = "above",
    ) -> dict:
        """Forecast a metric series and project ETA to threshold (v0.6)."""
        return self.request("GET", "/forecast", params={
            "model_id": model_id, "model_version": model_version,
            "metric": metric, "horizon": horizon, "threshold": threshold,
            "direction": direction,
        })

    # ----------------------------------------------------------- v0.8 ---
    def mmd(
        self,
        reference: list,
        current: list,
        *,
        bandwidth: float | None = None,
        n_permutations: int = 200,
        warn: float = 0.05,
        alert: float = 0.10,
    ) -> dict:
        """Embedding-drift via squared MMD with an RBF kernel (v0.8)."""
        return self.request("POST", "/mmd", json_body={
            "reference": list(reference), "current": list(current),
            "bandwidth": bandwidth, "n_permutations": int(n_permutations),
            "warn": warn, "alert": alert,
        })

    def cusum(
        self,
        *,
        model_id: str,
        model_version: str,
        metric: str,
        target: float | None = None,
        sigma: float | None = None,
        threshold: float = 4.0,
        slack_k: float = 0.5,
        limit: int = 200,
    ) -> dict:
        """Run an offline CUSUM over a stored metric history (v0.8)."""
        return self.request("POST", "/cusum", json_body={
            "model_id": model_id, "model_version": model_version,
            "metric": metric, "target": target, "sigma": sigma,
            "threshold": threshold, "slack_k": slack_k, "limit": limit,
        })

    def events(self, *, limit: int = 100, type: str | None = None) -> list[dict]:
        """Replay recent events from the server's in-process bus (v0.8)."""
        return self.request("GET", "/events", params={"limit": limit, "type": type})

    def stream_events(
        self,
        *,
        timeout: float | None = None,
    ):  # pragma: no cover — exercised against a live server
        """Yield live events from ``/ws/events`` as Python dicts (v0.8).

        Requires the optional ``websockets`` package. Yields one dict per
        event; the caller decides when to stop iterating.
        """
        try:
            from websockets.sync.client import connect  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError(
                "stream_events requires the 'websockets' package: "
                "`pip install websockets`."
            ) from e
        url = self.base_url.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1,
        ) + "ws/events"
        if self.api_key:
            url += f"?api_key={self.api_key}"
        with connect(url, open_timeout=timeout, close_timeout=timeout) as ws:
            while True:
                msg = ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    msg = msg.decode("utf-8")
                yield json.loads(msg)
