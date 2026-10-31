"""Scheduled analyses: "when search 1 finishes, analyze its results (or its lot)".

A scheduled search (``schedule_state='waiting'``) has no lot yet. As soon as no
analysis is running in the project and its origin search has finished an analysis,
its lot is computed (criteria applied to the origin's results, or the same lot)
and the analysis starts.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    InferenceJob,
    InferenceJobStatus,
    Project,
    Search,
    SearchArticle,
    User,
)
from app.model_access import usable_models
from app.searches import in_search

logger = logging.getLogger(__name__)


async def _fail(db: AsyncSession, s: Search, error: str) -> None:
    s.schedule = {**(s.schedule or {}), "error": error}
    s.schedule_state = "failed"
    await db.commit()


async def start_ready(db: AsyncSession, project: Project) -> str | None:
    """Starts the first ready scheduled analysis (job id), else None. One at a time."""
    from app.api.v1.prompts import ensure_prompt
    from app.api.v1.searches import Selection, select_articles
    from app.tasks.inference import inference_task

    busy = (
        await db.execute(
            select(InferenceJob.id)
            .where(
                InferenceJob.project_id == project.id,
                InferenceJob.status.in_([InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if busy is not None:
        return None
    waiting = (
        (
            await db.execute(
                select(Search)
                .where(Search.project_id == project.id, Search.schedule_state == "waiting")
                .order_by(Search.created_at, Search.id)
            )
        )
        .scalars()
        .all()
    )
    for s in waiting:
        cfg = s.schedule or {}
        parent = await db.get(Search, s.parent_id) if s.parent_id else None
        if parent is None:
            await _fail(db, s, "origin_deleted")
            continue
        last = (
            (
                await db.execute(
                    select(InferenceJob)
                    .where(
                        InferenceJob.project_id == project.id,
                        in_search(InferenceJob.search_id, parent),
                    )
                    .order_by(InferenceJob.created_at.desc(), InferenceJob.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if last is None or last.status != InferenceJobStatus.COMPLETED:
            continue  # l'origine n'a pas (encore) fini : on attend
        ids = await select_articles(db, project, Selection(**cfg["selection"]))
        if not ids:
            await _fail(db, s, "empty_selection")
            continue
        user = await db.get(User, s.created_by) if s.created_by else None
        names = [n for n in (cfg.get("model"), cfg.get("prefilter_model")) if n]
        if user is None or len(await usable_models(db, user, names, project.id)) != len(set(names)):
            await _fail(db, s, "model_unavailable")
            continue
        db.add_all(SearchArticle(search_id=s.id, article_id=i) for i in ids)
        prompt = await ensure_prompt(db, project, s, cfg.get("instructions"))
        job = InferenceJob(
            project_id=project.id,
            search_id=s.id,
            options={"gold_examples": 0, "validate": False},
            prompt_template_id=prompt.id,
            model_used=cfg["model"],
            prefilter_model=cfg.get("prefilter_model"),
            status=InferenceJobStatus.PENDING,
        )
        db.add(job)
        s.schedule_state = "started"
        await db.commit()
        logger.info("scheduler: analysis of search %s started (job %s)", s.id, job.id)
        inference_task.delay(str(job.id))
        return str(job.id)
    return None


def selection_dict(sel, origin_id: uuid.UUID) -> dict:
    """Stored selection: criteria on the origin's results (or same lot if empty)."""
    return {**sel.model_dump(mode="json", exclude_none=True), "from_search_id": str(origin_id)}
