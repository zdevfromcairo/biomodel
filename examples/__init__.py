"""Synthetic pathology pipeline example.

Run with::

    python -m examples.pathology_pipeline.run_example

This produces a baseline + a "current" batch with intentionally injected
issues (a site shift, a calibration drift, and a couple of plausibility
violations) and writes a full HTML+Markdown+JSON report under
``reports_out/``.
"""
