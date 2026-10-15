#!/bin/sh
set -e

# Démarré en root : on répare les droits des volumes (créés par root avant ce changement), puis on
# relance ce même script sans privilèges (utilisateur « app »).
if [ "$(id -u)" = "0" ]; then
    chown -R app:app /app/models /app/exports 2>/dev/null || true
    exec gosu app "$0" "$@"
fi

# Worker (celery) : pas de migration ici (le backend l'applique), démarrage direct.
if [ "$1" = "celery" ]; then
    shift
    exec celery "$@"
fi

echo "==> Application des migrations Alembic..."
alembic upgrade head

echo "==> Démarrage de l'API (uvicorn)..."
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
