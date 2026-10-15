"""App factory : middleware (CORS, i18n) + router v1 (prefix /api/v1)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    admin,
    admin_users,
    annotations,
    auth,
    benchmarks,
    export,
    gold,
    inference,
    models,
    projects,
    prompts,
    team,
    users,
)
from app.core.bootstrap import bootstrap_admin, bootstrap_models
from app.core.config import get_settings, validate_settings
from app.i18n import I18nMiddleware

logger = logging.getLogger(__name__)

# F8 (spec §7) : l'app est derrière nginx qui pose X-Forwarded-For / X-Forwarded-Proto
# (pas de TLS local). get_client_ip (app/api/deps.py) se fie à X-Forwarded-For pour le
# rate limiting ; nginx doit être configuré pour poser ces headers de façon fiable.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Au démarrage : bootstrap du premier admin (idempotent, voir app/core/bootstrap.py)."""
    settings = get_settings()
    try:
        await bootstrap_admin(settings)
    except Exception:  # noqa: BLE001 - le bootstrap ne doit pas bloquer le démarrage
        logger.exception("Bootstrap admin en échec (continuer sans admin)")
    try:
        await bootstrap_models(settings)
    except Exception:  # noqa: BLE001
        logger.exception("Pré-enregistrement des modèles en échec (ignoré)")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    # F5 : fail-fast sur les secrets de dev par défaut (JWT_SECRET / ANON_SALT).
    validate_settings(settings)
    app = FastAPI(title="Diggypaper API", lifespan=lifespan)
    # Dernier ajouté = le plus externe : CORS d'abord (preflight), puis i18n.
    app.add_middleware(I18nMiddleware)
    # F3 : CORS uniquement si des origines sont configurées (défaut vide = pas de CORS,
    # le déploiement est same-origin derrière nginx).
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(admin_users.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(annotations.router, prefix="/api/v1")
    app.include_router(team.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(prompts.router, prefix="/api/v1")
    app.include_router(gold.router, prefix="/api/v1")
    app.include_router(benchmarks.router, prefix="/api/v1")
    app.include_router(inference.router, prefix="/api/v1")
    return app
