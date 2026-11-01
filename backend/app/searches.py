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

from app.db.models import Article, GoldArticle, Project, Search, SearchArticle


def definition_hash(question: str | None, fields: list[dict] | None) -> str:
    """Fingerprint of the definition (question + items): same expected answers, same reference."""
    blob = json.dumps([(question or "").strip(), fields], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def default_search(db: AsyncSession, project: Project) -> Search:
    """The project's default search (created on demand: whole corpus, project question)."""
    s = (
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
    if s is None:  # created on first read, from the project settings at that moment
        s = Search(
            project_id=project.id,
            name=await next_search_name(db, project.id, project.created_at),
            question=project.question,
            fields=project.fields,
            text_sections=project.text_sections,
            read_windows=project.read_windows,
            definition_hash=definition_hash(project.question, project.fields),
            is_default=True,
            lot_all=True,
        )
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


def in_search(col, search: Search):
    """Condition "this row belongs to the search"; NULL = default search."""
    if search.is_default:
        return or_(col == search.id, col.is_(None))
    return col == search.id


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
    initial = await default_search(db, project)
    if initial.definition_hash == search.definition_hash:
        cond = or_(cond, GoldArticle.search_id.is_(None))
    return and_(GoldArticle.project_id == project.id, cond)
