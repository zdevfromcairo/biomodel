"""Render Markdown and HTML monitoring reports via Jinja2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_report(context: dict[str, Any], *, fmt: str = "md") -> str:
    """Render a report. ``fmt`` is 'md' or 'html'."""
    env = _env()
    if fmt == "md":
        tmpl = env.get_template("report.md.j2")
    elif fmt == "html":
        tmpl = env.get_template("report.html.j2")
    else:
        raise ValueError("fmt must be 'md' or 'html'")
    return tmpl.render(**context)
