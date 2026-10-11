# syntax=docker/dockerfile:1
# ---------- todonow: Flask + SQLite task manager for a homelab ----------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="todonow" \
      org.opencontainers.image.description="Local BUJO-style task manager (Flask + Bootstrap)" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    APP_TIMEZONE=America/Sao_Paulo \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    PORT=8081

WORKDIR /app

# curl is used by the container healthcheck; tini reaps zombie processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root runtime user owning the data volume.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin todonow \
    && mkdir -p /data/backups \
    && chown -R todonow:todonow /data /app \
    && chmod +x /app/docker/entrypoint.sh

USER todonow

VOLUME ["/data"]
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
