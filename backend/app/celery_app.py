"""Celery app: Redis broker + result backend (env ``REDIS_URL``).

Tasks are declared in ``app.tasks`` (which imports this module), not here, to
avoid a circular import. ``task_always_eager`` is driven by the ``CELERY_EAGER``
env var (default ``False``): ``True`` in tests for inline execution without a
broker.
"""

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_EAGER = os.environ.get("CELERY_EAGER", "").strip().lower() in {"1", "true", "yes", "on"}

celery_app = Celery("diggypaper", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=CELERY_EAGER,
    # acks_late + Redis visibility_timeout (default 1 h): a longer CPU analysis would be
    # redelivered and run twice. 24 h covers the largest corpora.
    broker_transport_options={"visibility_timeout": 24 * 3600},
    # 0 / absent = no limit; otherwise the task fails cleanly after N seconds.
    task_soft_time_limit=int(os.environ.get("TASK_SOFT_TIME_LIMIT", "0")) or None,
    # Registers the tasks: the worker loads app.celery_app.celery_app, so its task
    # modules must be imported explicitly (otherwise "unregistered task").
    include=[
        "app.tasks",
        "app.tasks.benchmark",
        "app.tasks.crate_import",
        "app.tasks.export",
        "app.tasks.inference",
        "app.tasks.maintenance",
        "app.tasks.notify",
    ],
    # Beat built into the worker (single worker): disk alert + inactive projects.
    beat_schedule={
        "maintenance": {"task": "diggypaper.maintenance", "schedule": 6 * 3600.0},
    },
)


# --- Worker startup recovery --------------------------------------------------
# A worker restart kills the tasks in flight: their jobs would stay "RUNNING"
# forever and the UI would spin in the void. At startup, the ones that were
# running are marked interrupted (PENDING jobs stay queued and will be processed).
from celery.signals import worker_ready  # noqa: E402

_INTERRUPTED = "Interrompu par un redémarrage du serveur : relancez l'opération."


async def _mark_interrupted(engine=None) -> int:
    from datetime import UTC, datetime

    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db.base import make_engine
    from app.db.models import (
        Article,
        BenchmarkRun,
        BenchmarkStatus,
        ExportJob,
        ImportJob,
        ImportJobStatus,
        InferenceJob,
        InferenceJobStatus,
        Project,
        ProjectStatus,
    )

    owned = engine is None
    if owned:
        engine = make_engine(os.environ.get("DATABASE_URL", ""))
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            now = datetime.now(UTC)
            n = 0
            for model, running, failed in (
                (ImportJob, ImportJobStatus.RUNNING, ImportJobStatus.FAILED),
                (InferenceJob, InferenceJobStatus.RUNNING, InferenceJobStatus.FAILED),
            ):
                r = await s.execute(
                    update(model)
                    .where(model.status == running)
                    .values(status=failed, error=_INTERRUPTED, completed_at=now)
                )
                n += r.rowcount or 0
            r = await s.execute(
                update(ExportJob)
                .where(ExportJob.status == "RUNNING")
                .values(status="FAILED", error=_INTERRUPTED, completed_at=now)
            )
            n += r.rowcount or 0
            r = await s.execute(
                update(BenchmarkRun)
                .where(BenchmarkRun.status == BenchmarkStatus.RUNNING)
                .values(status=BenchmarkStatus.FAILED)
            )
            n += r.rowcount or 0
            # Interrupted import: the project becomes importable again (re-import deduplicates).
            busy = select(ImportJob.project_id).where(
                ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING])
            )
            stuck = (Project.status == ProjectStatus.PROCESSING, Project.id.not_in(busy))
            has_articles = select(Article.project_id)
            await s.execute(  # complément d'un corpus existant : on y revient
                update(Project)
                .where(*stuck, Project.id.in_(has_articles))
                .values(status=ProjectStatus.FROZEN)
            )
            await s.execute(update(Project).where(*stuck).values(status=ProjectStatus.DRAFT))
            await s.commit()
            return n
    finally:
        if owned:
            await engine.dispose()


@worker_ready.connect
def _recover_interrupted_jobs(**_kwargs) -> None:
    import asyncio
    import logging

    from app.core.config import get_settings, validate_settings

    log = logging.getLogger(__name__)

    # F5: same check as at API startup. The worker encrypts/decrypts keys in the
    # database: it must not run with dev secrets. Never blocking (the API, which
    # refuses to start, stays the reference) — an explicit log beats a traceback
    # at the first decryption.
    try:
        validate_settings(get_settings())
    except Exception as exc:  # noqa: BLE001
        log.error("secrets invalides côté worker : %s", exc)

    # One worker performs the recovery: with two workers (main + ingestion), the
    # one starting up would mark the other's RUNNING jobs FAILED.
    if os.environ.get("RECOVER_INTERRUPTED", "1").strip().lower() in {"0", "false", "no"}:
        log.info("récupération des jobs interrompus désactivée (RECOVER_INTERRUPTED=0)")
        return
    try:
        n = asyncio.run(_mark_interrupted())
        if n:
            log.warning("%d job(s) interrompu(s) marqués FAILED", n)
    except Exception:  # noqa: BLE001 - ne doit jamais empêcher le worker de démarrer
        log.exception("récupération des jobs interrompus en échec")
