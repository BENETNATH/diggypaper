#!/bin/sh
set -e

# Started as root: fix the volume permissions (created by root before this change), then
# re-run this same script without privileges (user "app"). Recursive chown only when the
# top-level owner is wrong (a full walk of the exports volume on every start is slow).
if [ "$(id -u)" = "0" ]; then
    for d in /app/models /app/exports /app/.cache /app/celerybeat; do
        [ ! -d "$d" ] || [ "$(stat -c %U "$d")" = app ] || chown -R app:app "$d" 2>/dev/null || true
    done
    exec gosu app "$0" "$@"
fi

# Worker (celery): no migration here (the backend applies it), direct start.
if [ "$1" = "celery" ]; then
    shift
    exec celery "$@"
fi

echo "==> Application des migrations Alembic..."
alembic upgrade head

echo "==> Démarrage de l'API (uvicorn)..."
# Proxy headers trusted from any peer: the backend is not published, only nginx reaches it
# (client IP for the rate limits, https scheme for the generated URLs).
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
