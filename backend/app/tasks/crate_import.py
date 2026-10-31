"""Background RO-Crate import: the uploaded zip is restored into its project."""

import asyncio
import logging
import os
import threading
import uuid
from datetime import UTC, datetime

from celery import current_task
from sqlalchemy.ext.asyncio import AsyncSession

from app import crate_import as ci
from app.celery_app import celery_app
from app.db.models import (
    Article,
    ArticleFile,
    ArticlePrediction,
    HumanAnnotation,
    ImportJob,
    ImportJobStatus,
    PredictionStatus,
    Project,
    ProjectStatus,
)
from app.inference import clean_meta
from app.schema import project_fields
from app.searches import default_search, definition_hash
from app.tasks import _make_session_factory, _session_factory_override

logger = logging.getLogger(__name__)
_BATCH = 25


@celery_app.task(name="diggypaper.import_crate")
def import_crate_task(project_id: str, path: str, user_id: str) -> dict:
    """Sync Celery wrapper: the async core runs in a dedicated thread (like the PubMed import)."""
    job_id = current_task.request.id
    out: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal out, error
        try:
            out = asyncio.run(_import_crate(project_id, path, user_id, job_id=job_id))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=_run, name="import-crate")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return out


def _decision(answers, extra: dict) -> dict:
    """Non-empty answers + auxiliary fields (confidences, summary, relevance…)."""
    out = {k: v for k, v in (answers or {}).items() if v is not None}
    return out | {k: v for k, v in extra.items() if v is not None}


async def _import_crate(
    project_id: str, path: str, user_id: str, session_factory=None, job_id: str | None = None
) -> dict:
    session_factory = session_factory or _session_factory_override
    engine = None
    if session_factory is None:
        engine, session_factory = _make_session_factory()
    try:
        async with session_factory() as session:
            project = await session.get(Project, uuid.UUID(project_id))
            if project is None:
                raise LookupError(f"project not found: {project_id}")
            job = ImportJob(
                project_id=project.id,
                query="RO-Crate",
                kind="crate",
                job_id=job_id,
                status=ImportJobStatus.RUNNING,
                result_json={"phase": "importing", "done": 0, "total": 0},
            )
            session.add(job)
            await session.commit()
            try:
                return await _restore(session, project, job, path, uuid.UUID(user_id))
            except Exception as exc:
                await session.rollback()
                message = str(exc) if isinstance(exc, ci.CrateError) else type(exc).__name__
                logger.error("crate import failed (project %s): %s", project_id, message)
                job.status = ImportJobStatus.FAILED
                job.error = message
                job.completed_at = datetime.now(UTC)
                project.status = ProjectStatus.DRAFT
                await session.commit()
                raise
    finally:
        if engine is not None:
            await engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


async def _restore(
    session: AsyncSession, project: Project, job: ImportJob, path: str, user_id: uuid.UUID
) -> dict:
    zf, info = ci.open_crate(path)
    try:
        definition = ci.project_definition(info)
        project.question = definition["question"]
        project.fields = definition["fields"]
        project.text_sections = definition["text_sections"]
        search = await default_search(session, project)
        search.question, search.fields = project.question, project.fields
        search.text_sections = project.text_sections
        search.definition_hash = definition_hash(search.question, search.fields)
        field_keys = [f["key"] for f in project_fields(project)]
        keys = ci.article_keys(zf)
        job.result_json = {"phase": "importing", "done": 0, "total": len(keys)}
        await session.commit()
        counts = {
            "imported": 0,
            "failed": 0,
            "fulltext": 0,
            "pdf": 0,
            "predictions": 0,
            "reviews": 0,
        }
        done = 0
        for key in keys:
            try:
                rec = ci.read_article(zf, key)
            except ci.CrateError:
                counts["failed"] += 1
                done += 1
                continue
            ai, humans, pdf = rec.pop("ai"), rec.pop("human"), rec.pop("pdf")
            art = Article(project_id=project.id, origin="crate", origin_ref=str(job.id), **rec)
            if art.sections and not art.fulltext_source:
                art.fulltext_source = "crate"
            session.add(art)
            await session.flush()
            counts["imported"] += 1
            counts["fulltext"] += bool(art.sections)
            if pdf:
                session.add(ArticleFile(article_id=art.id, data=pdf))
                counts["pdf"] += 1
            if ai:
                answers = {k: (ai.get("answers") or {}).get(k) for k in field_keys}
                certainty = {k: v for k, v in (ai.get("certainty") or {}).items() if v is not None}
                try:
                    status = PredictionStatus(ai.get("status"))
                except ValueError:
                    status = None
                relevance = ai.get("relevance") if isinstance(ai.get("relevance"), float) else None
                quotes = (
                    ai.get("evidence_quotes")
                    if isinstance(ai.get("evidence_quotes"), list)
                    else None
                )
                session.add(
                    ArticlePrediction(
                        article_id=art.id,
                        search_id=search.id,
                        # untrusted input: no control characters (RIS/CSV injection)
                        model_used=clean_meta(ai.get("model")) or "RO-Crate",
                        model_identifier=clean_meta(ai.get("model_identifier"), 500),
                        model_version=clean_meta(ai.get("model_version"), 200),
                        decision_json=_decision(
                            answers,
                            {
                                "field_confidence": certainty or None,
                                "reasoning_summary": ai.get("reasoning"),
                                "confidence_score": relevance,
                                "evidence_quotes": quotes,
                            },
                        ),
                        confidence_score=relevance,
                        evidence_quotes=quotes,
                        status=status or PredictionStatus.NEEDS_HUMAN_REVIEW,
                    )
                )
                counts["predictions"] += 1
            for h in humans:
                session.add(
                    HumanAnnotation(
                        article_id=art.id,
                        user_id=user_id,
                        search_id=search.id,
                        decision_json=_decision(
                            {k: (h.get("answers") or {}).get(k) for k in field_keys}, {}
                        ),
                        notes=ci._clean(h.get("notes"), 5000),
                    )
                )
                counts["reviews"] += 1
            done += 1
            if done % _BATCH == 0:
                job.result_json = {"phase": "importing", "done": done, "total": len(keys), **counts}
                await session.commit()
        result = {**counts, "total_count": len(keys), "truncated": False}
        project.status = ProjectStatus.FROZEN
        job.status = ImportJobStatus.COMPLETED
        job.result_json = result
        job.completed_at = datetime.now(UTC)
        await session.commit()
        return result
    finally:
        zf.close()
