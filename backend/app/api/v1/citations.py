"""Enrich the corpus by citation chaining: references and citing articles of the seed articles.

The seeds are articles of the project (a selection, like when creating a search), with no
prior analysis. The candidates most linked to the seeds enter the corpus with their
origin; a search is created over them, to analyze later with the model to keep or discard.
"""

import uuid
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import citations
from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import (
    IMPORT_RATE_LIMIT,
    _require_lead_or_admin,
    _require_no_active_job,
    _require_project_member,
)
from app.api.v1.searches import Selection, select_articles
from app.celery_app import celery_app
from app.db.models import Article, ProjectStatus, User
from app.pubmed import _esearch_max
from app.tasks import import_pubmed_task

router = APIRouter(prefix="/projects", tags=["citations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

MAX_SEEDS = 2000


class CitationRequest(BaseModel):
    selection: Selection = Selection()  # starting articles (default: the whole corpus)
    direction: Literal["backward", "forward", "both"] = "both"  # references / citers / both
    min_overlap: int = Field(default=1, ge=1, le=50)  # minimum number of linked seeds
    max_candidates: int = Field(default=300, ge=1, le=10000)
    dry_run: bool = False  # estimation seulement


async def _seeds(db: AsyncSession, project, sel: Selection) -> tuple[list[str], int]:
    """(PMIDs of the seeds, number of selected articles); 422 if too many seeds."""
    ids = await select_articles(db, project, sel)
    pmids = [
        p
        for p in (await db.execute(select(Article.pmid).where(Article.id.in_(ids)))).scalars()
        if p
    ]
    if not pmids:
        raise HTTPException(status_code=422, detail="no seed with a PMID")
    if len(pmids) > MAX_SEEDS:
        raise HTTPException(status_code=422, detail=f"too many seeds ({len(pmids)} > {MAX_SEEDS})")
    return pmids, len(ids)


@router.post(
    "/{project_id}/citations",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("import", IMPORT_RATE_LIMIT))],
)
async def expand_by_citations(
    project_id: uuid.UUID, payload: CitationRequest, db: DbDep, user: UserDep
) -> dict:
    """``dry_run``: how many candidates (per overlap threshold). Otherwise a background task:
    import of the best candidates + search created over them (id in the job's result)."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    seeds, selected = await _seeds(db, project, payload.selection)
    if payload.dry_run:
        known = set(
            (await db.execute(select(Article.pmid).where(Article.project_id == project.id)))
            .scalars()
            .all()
        )
        async with httpx.AsyncClient(timeout=30) as http:
            links = await citations.fetch_links(seeds, http, payload.direction)
        return {
            "selected": selected,
            "seeds": len(seeds),
            "candidates": sum(1 for p in links if p not in known),
            "by_overlap": citations.overlap_histogram(links, known),
            "max": _esearch_max(),
        }
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="import already in progress")
    await _require_no_active_job(db, project.id)
    origin = payload.selection.from_search_id
    job = import_pubmed_task.delay(
        str(project.id),
        f"citations ({payload.direction}, {len(seeds)})",
        None,
        {
            "kind": "citations",
            "citations": {
                "seeds": seeds,
                "direction": payload.direction,
                "min_overlap": payload.min_overlap,
                "max_candidates": min(payload.max_candidates, _esearch_max()),
                "search_id": str(origin) if origin else None,
            },
        },
    )
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"job_id": job.id, "status": status}
