# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 biomodel

WORKDIR /app

# Copy only metadata first to leverage layer cache
COPY pyproject.toml README.md ./
COPY biomodel_monitor ./biomodel_monitor
COPY examples ./examples

RUN pip install --no-cache-dir ".[server,parquet,dashboard]"

USER biomodel

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8080/health || exit 1

ENV BIOMODEL_STORE_PATH=/data/biomodel.db
VOLUME ["/data"]

ENTRYPOINT ["biomodel-monitor"]
CMD ["serve", "--store", "/data/biomodel.db", "--host", "0.0.0.0", "--port", "8080", "--no-auth"]
