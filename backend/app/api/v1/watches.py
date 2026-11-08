"""Corpus watch (veille automatisée): a lead re-runs a source query on a cadence,
the new articles are imported + analyzed against a search's criteria, and the lead
is alerted when some match.

Read: member. Write: LEAD. The watch is an alert, not a silent acceptance: imported
articles (``origin="watch"``) enter the project and follow the existing review flow.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import _require_lead_or_admin, _require_project_member
from app.db.models import Project, Search, User, Watch, WatchStatus
from app.sources import source_name
from app.tasks import ADAPTERS
from app.tasks.watch import WatchCriteriaError, _has_criteria, run_watch_now

router = APIRouter(prefix="/projects", tags=["watches"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

SOURCES = tuple(sorted(ADAPTERS))

#: One manual pass = an import + an inference launch: same limit as the inference trigger.
WATCH_RUN_RATE_LIMIT = 5


class Criterion(BaseModel):
    """One alert criterion (spec 2026-11-08-criteres-veille).

    ``kind="field"``: an answer field of the watch's search (boolean or choice
    — text fields are not eligible) with the expected answer and an optional
    [conf_min, conf_max] window on the article confidence. ``kind="free"``: a
    free yes/no question, answered by the pass as a boolean field.
    """

    kind: Literal["field", "free"]
    field_key: str | None = None
    question: str | None = None
    expected: bool | str
    conf_min: float | None = Field(default=None, ge=0, le=1)
    conf_max: float | None = Field(default=None, ge=0, le=1)


def _validate_criteria(criteria: list[Criterion], search: Search) -> None:
    """Raises ValueError (mapped to a 422) on any criterion invalid against the
    search's schema. ``search.fields is None`` → no field criterion is possible."""
    by_key = {f["key"]: f for f in (search.fields or [])}
    seen_questions: set[str] = set()
    for c in criteria:
        if c.kind == "field":
            field = by_key.get(c.field_key or "")
            if field is None or field.get("type") not in ("boolean", "choice"):
                raise ValueError(f"invalid criterion field: {c.field_key}")
            if field["type"] == "boolean":
                if not isinstance(c.expected, bool):
                    raise ValueError(f"boolean field expects a boolean answer: {c.field_key}")
            else:
                options = [o.strip().lower() for o in field.get("options") or []]
                if not isinstance(c.expected, str) or c.expected.strip().lower() not in options:
                    raise ValueError(f"unknown option for {c.field_key}: {c.expected}")
        else:
            q = (c.question or "").strip()
            if not q or len(q) > 500:
                raise ValueError("free question must be 1-500 characters")
            if not isinstance(c.expected, bool):
                raise ValueError("a free question expects a yes/no answer")
            if q.lower() in seen_questions:
                raise ValueError(f"duplicated free question: {q}")
            seen_questions.add(q.lower())
        if c.conf_min is not None and c.conf_max is not None and c.conf_min > c.conf_max:
            raise ValueError("conf_min must be <= conf_max")


class WatchCreate(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    query: str = Field(min_length=1, max_length=2000)
    date_from: date | None = None
    search_id: uuid.UUID
    notify_all: bool = False
    criteria: list[Criterion] | None = None  # None = legacy rule
    combine: Literal["AND", "OR"] | None = None  # None = AND

    def __init__(self, **data: object) -> None:  # noqa: D105 — validate the source early
        super().__init__(**data)
        if self.source not in ADAPTERS:
            raise ValueError(f"unknown source: {self.source}")


class WatchUpdate(BaseModel):
    """Restricted edit (spec decision): only the criteria, the AND/OR combine
    and notify_all. source / query / search_id / date_from are frozen after
    creation — to change them, create a new watch. match_threshold is legacy
    only (watches with criteria is NULL) and is no longer writable."""

    status: Literal["ACTIVE", "PAUSED"] | None = None
    notify_all: bool | None = None
    criteria: list[Criterion] | None = None  # None = unchanged, [] = cleared
    combine: Literal["AND", "OR"] | None = None


async def _get_watch(db: AsyncSession, project: Project, watch_id: uuid.UUID) -> Watch:
    w = await db.get(Watch, watch_id)
    if w is None or w.project_id != project.id:
        raise HTTPException(status_code=404, detail="watch not found")
    return w


async def _full(db: AsyncSession, w: Watch) -> dict:
    search = await db.get(Search, w.search_id)
    author = await db.get(User, w.created_by) if w.created_by else None
    return {
        "id": str(w.id),
        "source": w.source,
        "source_name": source_name(w.source),
        "query": w.query,
        "date_from": w.date_from.isoformat() if w.date_from else None,
        "search": {"id": str(search.id), "name": search.name} if search else None,
        "match_threshold": w.match_threshold,
        "notify_all": w.notify_all,
        "criteria": w.criteria,
        "combine": w.combine,
        "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None,
        "last_matched": w.last_matched,
        "status": w.status,
        "created_by": (author.full_name or author.email) if author else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@router.get("/{project_id}/watches")
async def list_watches(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    project = await _require_project_member(db, project_id, user)
    rows = (
        (
            await db.execute(
                select(Watch)
                .where(Watch.project_id == project.id)
                .order_by(Watch.created_at, Watch.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [await _full(db, w) for w in rows]}


@router.post("/{project_id}/watches", status_code=201)
async def create_watch(
    project_id: uuid.UUID, payload: WatchCreate, db: DbDep, user: UserDep
) -> dict:
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    search = await db.get(Search, payload.search_id)
    if search is None or search.project_id != project.id:
        raise HTTPException(status_code=404, detail="search not found")
    if not _has_criteria(search):
        raise HTTPException(status_code=422, detail="search_without_criteria")
    try:
        _validate_criteria(payload.criteria or [], search)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    w = Watch(
        project_id=project.id,
        search_id=search.id,
        source=payload.source,
        query=payload.query,
        date_from=payload.date_from,
        notify_all=payload.notify_all,
        criteria=(
            [c.model_dump(exclude_none=True) for c in payload.criteria]
            if payload.criteria is not None
            else None
        ),
        combine=payload.combine,
        created_by=user.id,
        status=WatchStatus.ACTIVE,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return await _full(db, w)


@router.patch("/{project_id}/watches/{watch_id}")
async def update_watch(
    project_id: uuid.UUID, watch_id: uuid.UUID, payload: WatchUpdate, db: DbDep, user: UserDep
) -> dict:
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    w = await _get_watch(db, project, watch_id)
    if payload.status is not None:
        w.status = WatchStatus(payload.status)
    if payload.notify_all is not None:
        w.notify_all = payload.notify_all
    if payload.criteria is not None:
        search = await db.get(Search, w.search_id)
        if search is None:
            # Normally unreachable (search deletion cascades to its watches);
            # a dangling reference must 404 cleanly, never 500 in validation.
            raise HTTPException(status_code=404, detail="search not found")
        try:
            _validate_criteria(payload.criteria, search)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        w.criteria = [c.model_dump(exclude_none=True) for c in payload.criteria]
    if payload.combine is not None:
        w.combine = payload.combine
    await db.commit()
    await db.refresh(w)
    return await _full(db, w)


@router.delete("/{project_id}/watches/{watch_id}", status_code=204)
async def delete_watch(
    project_id: uuid.UUID, watch_id: uuid.UUID, db: DbDep, user: UserDep
) -> None:
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    w = await _get_watch(db, project, watch_id)
    await db.delete(w)
    await db.commit()


@router.post(
    "/{project_id}/watches/{watch_id}/run",
    dependencies=[Depends(_rate_limit_dependency("watch", WATCH_RUN_RATE_LIMIT))],
)
async def run_watch_endpoint(
    project_id: uuid.UUID, watch_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Run one watch pass now (instead of waiting for the cadence). LEAD only."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    w = await _get_watch(db, project, watch_id)
    try:
        await run_watch_now(db, w, launched_by=user)
    except WatchCriteriaError as err:
        # The watch's search lost its criteria (no question, no fields): nothing
        # to analyze — a clean 422 instead of a 500.
        raise HTTPException(status_code=422, detail="search_without_criteria") from err
    await db.refresh(w)
    return await _full(db, w)
