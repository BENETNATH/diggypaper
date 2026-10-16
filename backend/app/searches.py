"""Recherches : résolution (par défaut ou explicite), lot d'articles, empreinte de la question.

Une recherche porte sa question, ses items et ses parties de texte ; ses prédictions, relectures,
analyses et références lui sont rattachées. ``search_id IS NULL`` signifie « recherche par défaut
du projet » (données antérieures aux recherches).
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
    """Empreinte de la définition (question + items) : mêmes réponses attendues, même référence."""
    blob = json.dumps([(question or "").strip(), fields], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def default_search(db: AsyncSession, project: Project) -> Search:
    """Recherche par défaut du projet (créée à la demande : tout le corpus, question du projet)."""
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
    if s is None:  # créée à la première lecture, à partir des réglages du projet à cet instant
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
    """Nom par défaut d'une recherche : ``AAMMJJ-n`` (jour de création, n = rang dans le projet)."""
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
    """La recherche demandée (404 si elle n'est pas de ce projet) ou la recherche par défaut."""
    if search_id is None:
        return await default_search(db, project)
    s = await db.get(Search, search_id)
    if s is None or s.project_id != project.id:
        raise HTTPException(status_code=404, detail="search not found")
    return s


def in_search(col, search: Search):
    """Condition « cette ligne appartient à la recherche » ; NULL = recherche par défaut."""
    if search.is_default:
        return or_(col == search.id, col.is_(None))
    return col == search.id


def lot_condition(search: Search):
    """Condition SQL sur ``Article`` : l'article fait partie du lot de la recherche."""
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
    """Le projet vu à travers une recherche : question, items, parties de texte et fenêtres de
    lecture sont ceux de la recherche, le reste (titre, id, langue…) celui du projet. Permet de
    passer une recherche aux exports et au RO-Crate sans changer leur code."""

    _OVERRIDES = ("question", "fields", "text_sections", "read_windows")

    def __init__(self, project: Project, search: Search) -> None:
        self._project, self.search = project, search

    def __getattr__(self, name: str):
        return getattr(self.search if name in self._OVERRIDES else self._project, name)


async def gold_condition(db: AsyncSession, project: Project, search: Search):
    """Références (gold) qui valent pour cette recherche : celles de toute recherche du projet
    dont la définition (question + items) est identique — la vérité dépend de la question, pas du
    run. ``search_id`` NULL = recherche initiale."""
    same = select(Search.id).where(
        Search.project_id == project.id, Search.definition_hash == search.definition_hash
    )
    cond = GoldArticle.search_id.in_(same)
    initial = await default_search(db, project)
    if initial.definition_hash == search.definition_hash:
        cond = or_(cond, GoldArticle.search_id.is_(None))
    return and_(GoldArticle.project_id == project.id, cond)
