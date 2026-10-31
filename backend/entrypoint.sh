#!/bin/sh
set -e

# Started as root: fix the volume permissions (created by root before this change), then
# re-run this same script without privileges (user "app").
if [ "$(id -u)" = "0" ]; then
    chown -R app:app /app/models /app/exports 2>/dev/null || true
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
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
