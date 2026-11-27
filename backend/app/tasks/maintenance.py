"""Periodic maintenance task (celery beat, every 6 h): cf. ``app.maintenance``, plus the
worker-side housekeeping a killed task cannot do itself (stuck imports, orphan files,
expired exports)."""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import maintenance
from app.celery_app import _release_projects, celery_app
from app.db.models import ExportJob, ImportJob, ImportJobStatus
from app.tasks import _make_session_factory, _session_factory_override
from app.tasks.export import _purge
from app.tasks.inference import _make_mailer

logger = logging.getLogger(__name__)

#: An import still RUNNING after this long is dead (OOM-killed child: no ``finally``, no
#: restart recovery) — beyond the broker's 24 h visibility timeout anyway. ImportJob has
#: no progress timestamp, so its age stands in for "no progress".
# ponytail: age, not last progress; add a heartbeat column if an import may exceed 24 h
STALE_IMPORT_HOURS = 24
STALE_IMPORT = "Interrupted (no progress for too long): please rerun the import."

#: Files left by a SIGKILL/OOM (the task's ``finally`` never ran): ``*.part`` exports and
#: uploaded crates (``uploads/``, up to 1 GB each).
ORPHAN_HOURS = 24
#: Uploaded crates: their path is not stored in DB, a queued crate import (behind long
#: analyses on the main queue) must not lose its zip → much longer than ORPHAN_HOURS.
UPLOAD_ORPHAN_HOURS = 72


async def fail_stale_imports(session: AsyncSession, now: datetime | None = None) -> int:
    """RUNNING imports older than STALE_IMPORT_HOURS → FAILED; their project becomes
    importable again (same rule as the worker restart recovery)."""
    now = now or datetime.now(UTC)
    projects = (
        (
            await session.execute(
                update(ImportJob)
                .where(
                    ImportJob.status == ImportJobStatus.RUNNING,
                    # full-text completion never locks the project (no PROCESSING) and
                    # may legitimately exceed the threshold on a large corpus.
                    or_(ImportJob.kind.is_(None), ImportJob.kind != "fulltext"),
                    ImportJob.created_at < now - timedelta(hours=STALE_IMPORT_HOURS),
                )
                .values(status=ImportJobStatus.FAILED, error=STALE_IMPORT, completed_at=now)
                .returning(ImportJob.project_id)
            )
        )
        .scalars()
        .all()
    )
    if projects:
        await _release_projects(session, projects)
    await session.commit()
    return len(projects)


def purge_orphan_files(directory: Path, keep: set[str], now: float | None = None) -> int:
    """Deletes ``*.part`` older than ORPHAN_HOURS and ``uploads/*`` older than
    UPLOAD_ORPHAN_HOURS, except ``keep`` (file names of jobs still running)."""
    now = now or time.time()
    n = 0
    for path, hours in [
        *((p, ORPHAN_HOURS) for p in directory.glob("*.part")),
        *((p, UPLOAD_ORPHAN_HOURS) for p in (directory / "uploads").glob("*")),
    ]:
        try:
            recent = path.stat().st_mtime >= now - hours * 3600
            if path.name in keep or not path.is_file() or recent:
                continue
            path.unlink()
            n += 1
        except OSError:
            logger.warning("maintenance: orphan file not removed: %s", path.name)
    return n


async def housekeeping(session: AsyncSession) -> dict:
    """Stuck imports, orphan files, exports past their retention."""
    out = {"stale_imports": await fail_stale_imports(session)}
    directory = maintenance._export_dir()
    running = (
        (await session.execute(select(ExportJob.id).where(ExportJob.status == "RUNNING")))
        .scalars()
        .all()
    )
    out["orphan_files"] = purge_orphan_files(directory, {f"{i}.zip.part" for i in running})
    await _purge(session, directory)  # otherwise only run when a new export starts
    return out


async def _run(session_factory=None) -> dict:
    session_factory = session_factory or _session_factory_override
    engine = None
    if session_factory is None:
        engine, session_factory = _make_session_factory()
    try:
        async with session_factory() as session:
            out = await housekeeping(session)
            return out | await maintenance.run(session, await _make_mailer(session))
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(name="diggypaper.maintenance")
def maintenance_task() -> dict:
    result = asyncio.run(_run())
    logger.info("maintenance: %s", result)
    return result
