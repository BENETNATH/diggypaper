"""Celery app : broker + result backend Redis (env ``REDIS_URL``).

Les tasks sont déclarées dans ``app.tasks`` (qui importe ce module) — pas ici,
pour éviter un import circulaire. ``task_always_eager`` est piloté par l'env
``CELERY_EAGER`` (défaut ``False``) : ``True`` en tests pour exécution inline
sans broker.
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
    # acks_late + visibility_timeout Redis (défaut 1 h) : une analyse CPU plus longue serait
    # redélivrée et tournerait en double. 24 h couvre les plus gros corpus.
    broker_transport_options={"visibility_timeout": 24 * 3600},
    # 0 / absent = pas de limite ; sinon la tâche échoue proprement au bout de N secondes.
    task_soft_time_limit=int(os.environ.get("TASK_SOFT_TIME_LIMIT", "0")) or None,
    # Enregistre les tasks : le worker charge app.celery_app.celery_app, il faut
    # lui importer explicitement les modules de tâches (sinon "unregistered task").
    include=[
        "app.tasks",
        "app.tasks.benchmark",
        "app.tasks.export",
        "app.tasks.inference",
        "app.tasks.maintenance",
        "app.tasks.notify",
    ],
    # Beat intégré au worker (un seul worker) : alerte disque + projets inactifs.
    beat_schedule={
        "maintenance": {"task": "diggypaper.maintenance", "schedule": 6 * 3600.0},
    },
)


# --- Récupération au démarrage du worker --------------------------------------
# Un redémarrage du worker tue les tâches en cours : leurs jobs resteraient « RUNNING »
# à vie et l'interface tournerait dans le vide. Au démarrage, ceux qui tournaient sont
# déclarés interrompus (les jobs PENDING restent en file et seront traités).
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
            # Import interrompu : le projet redevient importable (le re-import dédoublonne).
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

    try:
        n = asyncio.run(_mark_interrupted())
        if n:
            logging.getLogger(__name__).warning("%d job(s) interrompu(s) marqués FAILED", n)
    except Exception:  # noqa: BLE001 - ne doit jamais empêcher le worker de démarrer
        logging.getLogger(__name__).exception("récupération des jobs interrompus en échec")
