# Contributing to BioModel Monitor

Thanks for your interest! BioModel Monitor is built to be auditable medical
software, so contributions go through a few lightweight gates that protect
the parts that matter most for clinical users.

## Ground rules

- **Be kind.** Treat reviewers and maintainers the way you'd want to be
  treated. Disagreement is welcome; ad-hominem isn't.
- **Small PRs win.** Aim for diffs reviewers can read in one sitting. Split
  refactors from feature work.
- **Tests are part of the change.** Every public function has at least one
  unit test; every endpoint has at least one integration test.
- **No secrets.** Don't commit credentials, real PHI, or anything else you
  wouldn't put on a billboard.

## Setting up

```bash
git clone https://github.com/zdevfromcairo/biomodel.git
cd biomodel
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
```

## Validating a change

The same three commands the CI runs:

```bash
ruff check biomodel_monitor tests       # lint
python -m pytest                         # tests (currently 200+)
mkdocs build --strict                    # docs build clean
```

The bundled example must keep working end-to-end:

```bash
python -m examples.pathology_pipeline.run_example
```

## Conventions

- Public dataclasses returned by metric modules expose `.severity` (one of
  `ok` / `warn` / `alert`) and an `.as_dict()` method. The alert engine
  consumes this contract — see `metrics/drift.py` for the canonical shape.
- Server endpoints that touch the filesystem must go through
  `_safe_batch_path` in `biomodel_monitor/server/app.py`.
- New CLI commands live in `biomodel_monitor/cli.py`. Group related v0.7+
  commands under `@main.group(...)`.
- Documentation pages go in `docs/` and are listed in `mkdocs.yml`.

## Reporting bugs

Open an issue with:

1. The version (`biomodel-monitor --version`).
2. A minimal reproduction (a few lines of synthetic data is fine).
3. What you expected and what happened.

For security-sensitive reports, see `SECURITY.md` (if present) or contact a
maintainer privately rather than opening a public issue.
