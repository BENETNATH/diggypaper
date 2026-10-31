"""Background RO-Crate export: zip written to EXPORT_DIR, email at the end.

Same schema as inference: sync Celery wrapper → thread + ``asyncio.run``. Zips
older than 7 days are purged at each new export.
"""

import asyncio
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.models import ExportJob, Project, User
from app.i18n import t
from app.searches import ScopedProject, get_search
from app.tasks import inference as _inf

logger = logging.getLogger(__name__)

KEEP_DAYS = 7
_session_factory_override: async_sessionmaker[AsyncSession] | None = None


@celery_app.task(name="diggypaper.export_crate")
def export_task(export_job_id: str) -> dict:
    """Sync Celery wrapper: the async core runs in a dedicated thread."""
    out: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal out, error
        try:
            out = asyncio.run(_run_export(export_job_id))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=_run, name="export-crate")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return out


async def _purge(session: AsyncSession, directory: Path) -> None:
    """Deletes exports older than KEEP_DAYS days (rows + files)."""
    cutoff = datetime.now(UTC) - timedelta(days=KEEP_DAYS)
    old = (
        (await session.execute(select(ExportJob).where(ExportJob.created_at < cutoff)))
        .scalars()
        .all()
    )
    for job in old:
        if job.filename:
            (directory / job.filename).unlink(missing_ok=True)
        await session.execute(delete(ExportJob).where(ExportJob.id == job.id))
    await session.commit()


async def _notify(session: AsyncSession, job: ExportJob, project: Project, ok: bool, stats: dict):
    """Email to the requester, in their language (best-effort: never blocking)."""
    try:
        user = await session.get(User, job.user_id) if job.user_id else None
        mailer = await _inf._make_mailer(session)
        if user is None or not user.email or mailer is None:
            return
        lang = user.language_preference or "en"
        link = f"{get_settings().app_base_url.rstrip('/')}/projects/{project.id}/articles"
        key = "export_ready" if ok else "export_failed"
        await mailer.send(
            user.email,
            t(lang, f"{key}_subject"),
            t(
                lang,
                f"{key}_body",
                project=project.title,
                link=link,
                articles=stats.get("articles", 0),
                pdf=stats.get("pdf", 0),
            ),
        )
    except Exception:
        logger.exception("export: notification e-mail échouée (job %s)", job.id)


async def _run_export(
    export_job_id: str, session_factory=None, directory: str | None = None
) -> dict:
    from app.api.v1.export import export_dir, write_crate

    session_factory = session_factory or _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _inf._make_session_factory()
    out_dir = Path(directory or export_dir())
    try:
        async with factory() as session:
            job = await session.get(ExportJob, uuid.UUID(export_job_id))
            if job is None:
                raise LookupError(f"export job not found: {export_job_id}")
            if job.status in ("COMPLETED", "FAILED"):
                return {}  # tâche redélivrée : déjà close
            project = await session.get(Project, job.project_id)
            scope = await get_search(session, project, job.search_id)
            project = ScopedProject(project, scope)
            user = await session.get(User, job.user_id) if job.user_id else None
            job.status, job.result_json = "RUNNING", {"done": 0, "total": 0}
            await session.commit()
            filename = f"{job.id}.zip"
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                await _purge(session, out_dir)
                start = time.monotonic()

                async def progress(done: int, total: int) -> None:
                    job.result_json = {"done": done, "total": total}
                    await session.commit()

                tmp = out_dir / f"{filename}.part"
                with tmp.open("wb") as fh:
                    stats = await write_crate(
                        session, project, user.email if user else "", fh, progress, scope
                    )
                tmp.replace(out_dir / filename)
                job.status, job.filename = "COMPLETED", filename
                job.result_json = {
                    **stats,
                    "size": (out_dir / filename).stat().st_size,
                    "seconds": round(time.monotonic() - start, 1),
                }
                job.completed_at = datetime.now(UTC)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception("export failed (job %s)", job.id)
                job = await session.get(ExportJob, job.id)
                job.status, job.error = "FAILED", type(exc).__name__
                job.completed_at = datetime.now(UTC)
                await session.commit()
                (out_dir / f"{filename}.part").unlink(missing_ok=True)
                await _notify(session, job, project, False, {})
                raise
            await _notify(session, job, project, True, job.result_json)
            return job.result_json
    finally:
        if engine is not None:
            await engine.dispose()
