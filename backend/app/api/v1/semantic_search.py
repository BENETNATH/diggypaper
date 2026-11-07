"""Semantic search endpoint (RAG, spec 2026-11-07-recherche-semantique-rag §4).

``POST /projects/{project_id}/semantic-search`` — hybrid (dense + lexical,
RRF) search over the project's vectorized sections. Read access for any
project member; the query embedding is CPU work, so the endpoint carries a
scoped rate limit.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import _require_project_member
from app.db.models import User
from app.semantic_search import DEFAULT_LIMIT, MAX_LIMIT, semantic_search

router = APIRouter(prefix="/projects", tags=["semantic-search"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

#: Interactive search (CPU embedding + scan): generous for use, bounded against
#: abuse (cf. the sibling trigger limits).
SEMANTIC_RATE_LIMIT = 30


class SemanticSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


@router.post(
    "/{project_id}/semantic-search",
    dependencies=[Depends(_rate_limit_dependency("semantic", SEMANTIC_RATE_LIMIT))],
)
async def semantic_search_endpoint(
    project_id: uuid.UUID, body: SemanticSearchBody, db: DbDep, user: UserDep
) -> dict:
    await _require_project_member(db, project_id, user)
    return await semantic_search(db, project_id, body.query, body.limit)


#: Corpus indexing is CPU work on the whole project — same trigger budget as
#: the inference/watch triggers.
VECTORIZE_RATE_LIMIT = 5


@router.post(
    "/{project_id}/vectorize",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("vectorize", VECTORIZE_RATE_LIMIT))],
)
async def vectorize_project_endpoint(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Manually (re)index the project corpus (« Indexer le corpus », spec §5) —
    lead/admin only. Enqueues the Celery task and returns its id (the task is
    incremental: unchanged sections are skipped)."""
    from app.api.v1.projects import _require_lead_or_admin
    from app.tasks.vectorize import vectorize_task

    await _require_lead_or_admin(db, project_id, user)
    result = vectorize_task.delay(str(project_id))
    return {"task_id": result.id, "queued": True}
