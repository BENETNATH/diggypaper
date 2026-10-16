"""Tâche périodique d'entretien (celery beat, toutes les 6 h) : cf. ``app.maintenance``."""

import asyncio
import logging

from app import maintenance
from app.celery_app import celery_app
from app.tasks import _make_session_factory, _session_factory_override
from app.tasks.inference import _make_mailer

logger = logging.getLogger(__name__)


async def _run(session_factory=None) -> dict:
    session_factory = session_factory or _session_factory_override
    engine = None
    if session_factory is None:
        engine, session_factory = _make_session_factory()
    try:
        async with session_factory() as session:
            return await maintenance.run(session, await _make_mailer(session))
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(name="diggypaper.maintenance")
def maintenance_task() -> dict:
    result = asyncio.run(_run())
    logger.info("maintenance: %s", result)
    return result
