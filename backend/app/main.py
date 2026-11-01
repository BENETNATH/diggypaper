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
    citations,
    crate_import,
    export,
    gold,
    inference,
    models,
    projects,
    prompts,
    query_assistant,
    searches,
    team,
    users,
)
from app.core.bootstrap import bootstrap_admin
from app.core.config import get_settings, validate_settings
from app.i18n import I18nMiddleware

logger = logging.getLogger(__name__)

# F8 (spec §7): the app is behind nginx which sets X-Forwarded-For / X-Forwarded-Proto
# (no local TLS). get_client_ip (app/api/deps.py) trusts X-Forwarded-For for the
# rate limiting; nginx must be configured to set these headers reliably.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """At startup: first-admin bootstrap (idempotent, see app/core/bootstrap.py)."""
    settings = get_settings()
    try:
        await bootstrap_admin(settings)
    except Exception:  # noqa: BLE001 - the bootstrap must not block startup
        logger.exception("Admin bootstrap failed (continuing without admin)")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    # F5: fail-fast on the dev default secrets (JWT_SECRET / ANON_SALT).
    validate_settings(settings)
    app = FastAPI(title="Diggypaper API", lifespan=lifespan)
    # Last added = outermost: CORS first (preflight), then i18n.
    app.add_middleware(I18nMiddleware)
    # F3: CORS only if origins are configured (default empty = no CORS,
    # the deployment is same-origin behind nginx).
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
    app.include_router(searches.router, prefix="/api/v1")
    app.include_router(citations.router, prefix="/api/v1")
    app.include_router(crate_import.router, prefix="/api/v1")
    app.include_router(query_assistant.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(prompts.router, prefix="/api/v1")
    app.include_router(gold.router, prefix="/api/v1")
    app.include_router(benchmarks.router, prefix="/api/v1")
    app.include_router(inference.router, prefix="/api/v1")

    @app.get("/api/v1/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    return app
