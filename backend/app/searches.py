"""Searches: resolution (default or explicit), article lot, fingerprint of the question.

A search carries its question, its items and its text parts; its predictions,
re-reads, analyses and references are attached to it. ``search_id IS NULL`` means
"the project's default search" (data predating searches).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, GoldArticle, InferenceJob, Project, Search, SearchArticle


def definition_hash(question: str | None, fields: list[dict] | None) -> str:
    """Fingerprint of the definition (question + items): same expected answers, same reference.

    ``badge`` (display opt-in, spec 2026-11-09-triage-seuils R3) is excluded:
    toggling it must not change the hash (locked question, gold sharing)."""
    norm = [{k: v for k, v in f.items() if k != "badge"} for f in (fields or [])]
    blob = json.dumps([(question or "").strip(), norm], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def _existing_default(db: AsyncSession, project: Project) -> Search | None:
    return (
        (
            await db.execute(
                select(Search)
                .where(Search.project_id == project.id, Search.is_default.is_(True))
                .order_by(Search.created_at, Search.id)
            )
        )
        .scalars()
        .first()
    )


async def _has_legacy_gold(db: AsyncSession, project: Project) -> bool:
    """Legacy references (search_id NULL) predating the default search."""
    return (
        await db.execute(
            select(GoldArticle.id)
            .where(GoldArticle.project_id == project.id, GoldArticle.search_id.is_(None))
            .limit(1)
        )
    ).first() is not None


def _new_default(project: Project, name: str) -> Search:
    return Search(
        project_id=project.id,
        name=name,
        question=project.question,
        fields=project.fields,
        text_sections=project.text_sections,
        read_windows=project.read_windows,
        definition_hash=definition_hash(project.question, project.fields),
        is_default=True,
        lot_all=True,
    )


async def _default_search(db: AsyncSession, project: Project) -> Search | None:
    """The project's default search — created from the project settings only when
    legacy gold rows (search_id NULL) need their house (R10); None otherwise
    (R9: no more on-the-fly auto-creation)."""
    s = await _existing_default(db, project)
    if s is None and await _has_legacy_gold(db, project):
        s = _new_default(project, await next_search_name(db, project.id, project.created_at))
        db.add(s)
        await db.flush()
    return s


async def default_search(db: AsyncSession, project: Project) -> Search:
    """The project's default search; 404 when the project has none (R9/R10)."""
    s = await _default_search(db, project)
    if s is None:
        raise HTTPException(status_code=404, detail="no default search in this project")
    return s


async def ensure_default_search(db: AsyncSession, project: Project) -> Search:
    """The project's default search, created from the project settings when absent —
    an EXPLICIT creation for the RO-Crate restore (R9: reads no longer auto-create)."""
    s = await _default_search(db, project)
    if s is None:
        s = _new_default(project, await next_search_name(db, project.id, project.created_at))
        db.add(s)
        await db.flush()
    return s


async def next_search_name(
    db: AsyncSession, project_id: uuid.UUID, day: datetime | None = None
) -> str:
    """Default name of a search: ``YYMMDD-n`` (creation day, n = rank in the project)."""
    prefix = (day or datetime.now(UTC)).strftime("%y%m%d")
    taken = set(
        (await db.execute(select(Search.name).where(Search.project_id == project_id)))
        .scalars()
        .all()
    )
    n = 1
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


async def get_search(
    db: AsyncSession, project: Project, search_id: uuid.UUID | None = None
) -> Search:
    """The requested search (404 if it is not of this project) or the default search."""
    if search_id is None:
        return await default_search(db, project)
    s = await db.get(Search, search_id)
    if s is None or s.project_id != project.id:
        raise HTTPException(status_code=404, detail="search not found")
    return s


async def resolve_search(
    db: AsyncSession, project: Project, search_id: uuid.UUID | None = None
) -> Search | None:
    """Search for a project-level endpoint (project detail, project-wide inference):
    the explicit search (404 if it is not of this project), else the default search —
    or ``None`` when the project has no default (R9: the project settings stand in)."""
    if search_id is not None:
        return await get_search(db, project, search_id)
    return await _default_search(db, project)


def in_search(col, search: Search):
    """Condition "this row belongs to the search"; NULL = default search."""
    if search.is_default:
        return or_(col == search.id, col.is_(None))
    return col == search.id


def job_in_search(job: InferenceJob, search: Search) -> bool:
    """True if the job belongs to the search (search_id NULL = the default search)."""
    if search.is_default:
        return job.search_id in (search.id, None)
    return job.search_id == search.id


def project_as_origin(project: Project) -> Search:
    """The project settings seen as a default search (whole-corpus lot).

    New projects have no default search (R9): a lot derived from the "initial
    search" that does not exist falls back to the project settings."""
    return Search(
        project_id=project.id,
        name="",
        question=project.question,
        fields=project.fields,
        text_sections=project.text_sections,
        read_windows=project.read_windows,
        is_default=True,
        lot_all=True,
    )


async def default_origin(db: AsyncSession, project: Project) -> Search:
    """Origin search for a lot "same as the initial search": the project's default
    search, or its settings themselves when it does not exist (R9)."""
    return await _default_search(db, project) or project_as_origin(project)


def lot_condition(search: Search):
    """SQL condition on ``Article``: the article is part of the search's lot."""
    if search.lot_all:
        return Article.project_id == search.project_id
    return and_(
        Article.project_id == search.project_id,
        Article.id.in_(
            select(SearchArticle.article_id).where(SearchArticle.search_id == search.id)
        ),
    )


async def lot_ids(db: AsyncSession, search: Search) -> list[uuid.UUID]:
    rows = await db.execute(select(Article.id).where(lot_condition(search)).order_by(Article.id))
    return list(rows.scalars().all())


class ScopedProject:
    """The project seen through a search: question, items, text parts and reading
    windows are those of the search, the rest (title, id, language…) that of the
    project. Lets you pass a search to the exports and RO-Crate without changing their code."""

    _OVERRIDES = ("question", "fields", "text_sections", "read_windows")

    def __init__(self, project: Project, search: Search) -> None:
        self._project, self.search = project, search

    def __getattr__(self, name: str):
        return getattr(self.search if name in self._OVERRIDES else self._project, name)


async def gold_condition(db: AsyncSession, project: Project, search: Search):
    """References (gold) that hold for this search: those of any project search
    with an identical definition (question + items) — the truth depends on the
    question, not the run. ``search_id`` NULL = initial search."""
    same = select(Search.id).where(
        Search.project_id == project.id, Search.definition_hash == search.definition_hash
    )
    cond = GoldArticle.search_id.in_(same)
    # R9: the project may have no default search (explicit searches only) — the
    # NULL legacy rows are only shared when a default with the same definition exists.
    initial = await _default_search(db, project)
    if initial is not None and initial.definition_hash == search.definition_hash:
        cond = or_(cond, GoldArticle.search_id.is_(None))
    return and_(GoldArticle.project_id == project.id, cond)
