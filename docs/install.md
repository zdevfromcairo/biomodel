# Install

BioModel Monitor is a Python 3.10+ package with optional extras for each
operational layer. The core install has *no* heavyweight runtime dependencies.

## From source

```bash
git clone https://github.com/zdevfromcairo/biomodel
cd biomodel
pip install -e .
```

## Optional extras

| Extra        | What you get                                        | When you need it                       |
| ------------ | --------------------------------------------------- | -------------------------------------- |
| `parquet`    | `pyarrow`                                           | Reading Parquet batches                |
| `dashboard`  | `streamlit`                                         | The triage dashboard                   |
| `server`     | `fastapi`, `uvicorn[standard]`, `httpx`             | Running `biomodel-monitor serve` (v0.4) |
| `postgres`   | `psycopg[binary]`                                   | Postgres metrics store backend (v0.4)  |
| `docs`       | `mkdocs`, `mkdocs-material`, `pymdown-extensions`   | Building this site locally             |
| `dev`        | Test, lint, type-check stack                        | Contributing                           |

Combine as needed:

```bash
pip install -e ".[dev,parquet,dashboard,server,docs]"
```

## Docker

```bash
docker build -t biomodel-monitor:dev .
docker run --rm -p 8080:8080 -v $(pwd)/data:/data biomodel-monitor:dev
```

Or use the bundled Compose stack — see the [Quickstart](quickstart.md).

## Verify the install

```bash
biomodel-monitor --help
python -m pytest -q
```
