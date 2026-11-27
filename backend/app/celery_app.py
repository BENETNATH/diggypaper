"""Celery app: Redis broker + result backend (env ``REDIS_URL``).

Tasks are declared in ``app.tasks`` (which imports this module), not here, to
avoid a circular import. ``task_always_eager`` is driven by the ``CELERY_EAGER``
env var (default ``False``): ``True`` in tests for inline execution without a
broker.
"""

import os

from celery import Celery
from celery.schedules import crontab

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
    # No task_soft_time_limit: SoftTimeLimitExceeded would hit the main thread blocked in
    # join() while the async core keeps running in its thread (two jobs in one child,
    # status never updated). TASK_SOFT_TIME_LIMIT is therefore not supported.
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
        "app.tasks.vectorize",
        "app.tasks.watch",
    ],
    # Beat built into the worker (single worker): disk alert + inactive projects.
    # crontab (wall clock, UTC), not intervals: a lost schedule file would restart the
    # intervals from the deploy time and push the watch back by up to 24 h each deploy.
    beat_schedule={
        "maintenance": {
            "task": "diggypaper.maintenance",
            "schedule": crontab(minute=0, hour="*/6"),
        },
        # Corpus watch: one pass per day (spec 2026-11-07-veille-automatisee).
        "watch": {"task": "diggypaper.watch", "schedule": crontab(minute=0, hour=3)},
    },
)


# --- Worker startup recovery --------------------------------------------------
# A worker restart kills the tasks in flight: their jobs would stay "RUNNING"
# forever and the UI would spin in the void. At startup, the ones that were
# running are marked interrupted (PENDING jobs stay queued and will be processed).
# Each worker only recovers the jobs of the queues it consumes: the other worker's
# are still running (imports on « ingest », the rest on the default queue).
from celery.signals import worker_ready  # noqa: E402

_INTERRUPTED = "Interrupted by a server restart: please rerun the operation."
_INGEST_QUEUE = "ingest"  # = app.tasks.INGEST_QUEUE (not imported: circular import)


async def _release_projects(s, project_ids=None) -> None:
    """PROCESSING projects without a live import become importable again (re-import
    deduplicates): FROZEN if they hold articles (top-up of a corpus), else DRAFT.
    ``project_ids``: only those projects (None = all)."""
    from sqlalchemy import select, update

    from app.db.models import Article, ImportJob, ImportJobStatus, Project, ProjectStatus

    busy = select(ImportJob.project_id).where(
        ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING]),
        ImportJob.project_id.is_not(None),  # a NULL in NOT IN would match nothing
    )
    stuck = [Project.status == ProjectStatus.PROCESSING, Project.id.not_in(busy)]
    if project_ids is not None:
        stuck.append(Project.id.in_(set(project_ids)))
    has_articles = select(Article.project_id)
    await s.execute(  # top-up of an existing corpus: back to FROZEN
        update(Project)
        .where(*stuck, Project.id.in_(has_articles))
        .values(status=ProjectStatus.FROZEN)
    )
    await s.execute(update(Project).where(*stuck).values(status=ProjectStatus.DRAFT))


async def _mark_interrupted(engine=None, queues=None) -> int:
    """``queues``: names of the queues consumed by this worker (None = all)."""
    from datetime import UTC, datetime

    from sqlalchemy import or_, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db.base import make_engine
    from app.db.models import (
        BenchmarkRun,
        BenchmarkStatus,
        ExportJob,
        ImportJob,
        ImportJobStatus,
        InferenceJob,
        InferenceJobStatus,
    )

    main = queues is None or celery_app.conf.task_default_queue in queues
    ingest = queues is None or _INGEST_QUEUE in queues
    # Crate restores run on the default queue; every other import kind on « ingest ».
    kinds = []
    if main:
        kinds.append(ImportJob.kind == "crate")
    if ingest:
        kinds.append(or_(ImportJob.kind.is_(None), ImportJob.kind != "crate"))
    owned = engine is None
    if owned:
        engine = make_engine(os.environ.get("DATABASE_URL", ""))
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as s:
            now = datetime.now(UTC)
            n = 0
            failed_projects: list = []
            if kinds:
                failed_projects = (
                    (
                        await s.execute(
                            update(ImportJob)
                            .where(ImportJob.status == ImportJobStatus.RUNNING, or_(*kinds))
                            .values(
                                status=ImportJobStatus.FAILED, error=_INTERRUPTED, completed_at=now
                            )
                            .returning(ImportJob.project_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                n += len(failed_projects)
            if main:
                r = await s.execute(
                    update(InferenceJob)
                    .where(InferenceJob.status == InferenceJobStatus.RUNNING)
                    .values(status=InferenceJobStatus.FAILED, error=_INTERRUPTED, completed_at=now)
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
            # Interrupted import: the project becomes importable again. The main worker
            # sweeps every stuck project (historical behavior); the ingest one only its own.
            await _release_projects(s, None if main else failed_projects)
            await s.commit()
            return n
    finally:
        if owned:
            await engine.dispose()


def _consumed_queues(consumer) -> set[str]:
    """Queues this worker consumes (``-Q``; default queue without it)."""
    try:
        return {q.name for q in consumer.task_consumer.queues}
    except AttributeError:  # no consumer at hand: the -Q selection
        return set(celery_app.amqp.queues.consume_from)


@worker_ready.connect
def _recover_interrupted_jobs(sender=None, **_kwargs) -> None:
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
        log.error("invalid secrets on the worker side: %s", exc)

    # Opt-out; otherwise each worker recovers the jobs of its own queues only.
    if os.environ.get("RECOVER_INTERRUPTED", "1").strip().lower() in {"0", "false", "no"}:
        log.info("recovery of interrupted jobs disabled (RECOVER_INTERRUPTED=0)")
        return
    try:
        n = asyncio.run(_mark_interrupted(queues=_consumed_queues(sender)))
        if n:
            log.warning("%d interrupted job(s) marked FAILED", n)
    except Exception:  # noqa: BLE001 - must never prevent the worker from starting
        log.exception("recovery of interrupted jobs failed")
