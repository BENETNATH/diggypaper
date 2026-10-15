#!/bin/sh
set -e

# Worker (celery) : pas de migration ici (le backend l'applique), démarrage direct.
if [ "$1" = "celery" ]; then
    shift
    exec celery "$@"
fi

echo "==> Application des migrations Alembic..."
alembic upgrade head

echo "==> Démarrage de l'API (uvicorn)..."
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
