# syntax=docker/dockerfile:1
# --------------------------------------------------------------------------
# Backend image.
#
# Runs as a non-root user with a read-only root filesystem in compose. The
# application only ever needs to read from the operational database, so the
# container is given no more capability than that.
# --------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq is needed by psycopg; curl is used by the container healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code changes do not invalidate the layer cache.
COPY apps/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app ./app
COPY apps/api/alembic.ini* ./
COPY scripts ./scripts

# Unprivileged runtime user.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
 && mkdir -p /app/data /app/exports \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Gunicorn supervises uvicorn workers: a worker that dies is replaced, and
# WEB_CONCURRENCY tunes worker count per host without rebuilding the image.
CMD ["sh", "-c", "gunicorn app.main:app \
      --worker-class uvicorn.workers.UvicornWorker \
      --workers ${WEB_CONCURRENCY:-4} \
      --bind 0.0.0.0:8000 \
      --timeout ${GUNICORN_TIMEOUT:-120} \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      --forwarded-allow-ips '*'"]
