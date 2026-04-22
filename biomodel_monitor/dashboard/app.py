"""Minimal Streamlit dashboard reading a pipeline JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _badge(sev: str) -> str:
    color = {"ok": "🟢", "warn": "🟡", "alert": "🔴"}.get(sev, "⚪")
    return f"{color} {sev}"


def main() -> None:  # pragma: no cover - thin UI layer
    try:
        import streamlit as st
    except ImportError as e:
        raise SystemExit(
            "streamlit is not installed. Install with `pip install biomodel-monitor[dashboard]`."
        ) from e
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="Path to pipeline JSON output")
    args, _ = parser.parse_known_args()
    data = _load(Path(args.json))

    st.set_page_config(page_title="BioModel Monitor", layout="wide")
    st.title("BioModel Monitor")

    page = st.sidebar.radio(
        "Pages",
        [
            "Overview",
            "Drift",
            "Calibration",
            "Subgroups",
            "Plausibility",
            "Alerts",
            "Incident Detail",
        ],
    )

    if page == "Overview":
        st.metric("Alerts", len(data.get("alerts", [])))
        st.metric("Drift metrics", len(data.get("drift_results", [])))
        st.metric("Calibration metrics", len(data.get("calibration_results", [])))
        st.metric("Subgroup slices", len(data.get("subgroup_results", [])))
        st.metric("Plausibility violations", len(data.get("plausibility_violations", [])))
    elif page == "Drift":
        st.dataframe(data.get("drift_results", []))
    elif page == "Calibration":
        st.dataframe(data.get("calibration_results", []))
    elif page == "Subgroups":
        st.dataframe(data.get("subgroup_results", []))
    elif page == "Plausibility":
        st.dataframe(data.get("plausibility_violations", []))
    elif page == "Alerts":
        for a in data.get("alerts", []):
            st.write(f"{_badge(a['severity'])} **{a['title']}**  · score {a['score']:.2f}")
            with st.expander("details"):
                st.json(a)
    elif page == "Incident Detail":
        st.write("Pick an alert to inspect:")
        titles = [a["title"] for a in data.get("alerts", [])]
        if not titles:
            st.info("No alerts in this batch.")
            return
        sel = st.selectbox("Alert", titles)
        for a in data.get("alerts", []):
            if a["title"] == sel:
                st.json(a)
                break


if __name__ == "__main__":  # pragma: no cover
    main()
