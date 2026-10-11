#!/bin/sh
# todonow container entrypoint: prepare the database, optionally bootstrap an
# admin account, then hand over to gunicorn.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
PORT="${PORT:-8081}"
export DATA_DIR

mkdir -p "${DATA_DIR}/backups"

echo "[todonow] applying database schema…"
if [ -d /app/migrations/versions ] && [ -n "$(ls -A /app/migrations/versions 2>/dev/null || true)" ]; then
    flask --app wsgi db upgrade
else
    flask --app wsgi init-db
fi

# Optional first-run admin bootstrap: only creates the account if it is missing.
if [ -n "${TODONOW_ADMIN_USER:-}" ] && [ -n "${TODONOW_ADMIN_PASSWORD:-}" ]; then
    echo "[todonow] ensuring admin user '${TODONOW_ADMIN_USER}' exists…"
    if [ -n "${TODONOW_ADMIN_EMAIL:-}" ]; then
        flask --app wsgi create-user "${TODONOW_ADMIN_USER}" \
            --password "${TODONOW_ADMIN_PASSWORD}" \
            --only-if-missing \
            --email "${TODONOW_ADMIN_EMAIL}" \
            --admin
    else
        flask --app wsgi create-user "${TODONOW_ADMIN_USER}" \
            --password "${TODONOW_ADMIN_PASSWORD}" \
            --only-if-missing \
            --admin
    fi
fi

if [ "${TODONOW_RUN_MIGRATIONS_ONLY:-0}" = "1" ]; then
    echo "[todonow] migrations only — exiting."
    exit 0
fi

echo "[todonow] starting gunicorn on 0.0.0.0:${PORT} (workers=${GUNICORN_WORKERS:-2}, threads=${GUNICORN_THREADS:-4})"
exec gunicorn \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --worker-class gthread \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}" \
    wsgi:app
