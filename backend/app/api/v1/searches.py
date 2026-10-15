"""Recherches d'un projet : une question (ses items) posée à un lot d'articles.

Plusieurs recherches par projet, sur le même lot ou sur un lot dérivé des résultats d'une autre
(« les articles où la recherche 1 a répondu oui »). Lecture : membre. Écriture : LEAD.
"""

import random
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.projects import (
    STATUS_PATTERN,
    _require_lead_or_admin,
    _require_project_member,
    article_filter,
)
from app.db.models import (
    Article,
    ArticlePrediction,
    GoldArticle,
    HumanAnnotation,
    InferenceJob,
    Project,
    Search,
    SearchArticle,
    User,
)
from app.schema import DEFAULT_QUESTION, check_fields, project_fields
from app.searches import default_search, definition_hash, get_search, in_search, lot_condition

router = APIRouter(prefix="/projects", tags=["searches"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


class Selection(BaseModel):
    """Quels articles : tout, ou ceux qui répondent à des critères (sur les résultats d'une
    recherche), un tirage aléatoire, ou une liste. Critères cumulés (ET)."""

    from_search_id: uuid.UUID | None = (
        None  # recherche dont on filtre les résultats (déf. initiale)
    )
    status: str | None = Field(default=None, pattern=STATUS_PATTERN)
    answers: dict[str, str | bool] | None = None  # {item: valeur}
    reviewed: bool | None = None  # relu par un humain (dans from_search)
    has_fulltext: bool | None = None
    confidence_min: float | None = Field(default=None, ge=0, le=1)
    confidence_max: float | None = Field(default=None, ge=0, le=1)
    ids: list[uuid.UUID] | None = Field(default=None, max_length=20000)
    sample_n: int | None = Field(default=None, ge=1, le=20000)  # tirage au sort parmi le résultat
    seed: int | None = None  # tirage reproductible


class SearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    question: str | None = Field(default=None, max_length=2000)
    fields: list[dict] | None = None
    text_sections: list[str] | None = None
    read_windows: int | None = Field(default=None, ge=1, le=32)
    # standard ; tuning (mise au point : compare variantes) ; blind (échantillon relu à l'aveugle)
    kind: Literal["standard", "tuning", "blind"] = "standard"
    selection: Selection = Selection()

    @field_validator("fields")
    @classmethod
    def _check(cls, v: list[dict] | None) -> list[dict] | None:
        return None if v is None else check_fields(v)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class SearchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


def _is_all(sel: Selection) -> bool:
    return not sel.model_dump(exclude_none=True)


async def select_articles(db: AsyncSession, project: Project, sel: Selection) -> list[uuid.UUID]:
    """Identifiants des articles du projet qui répondent à ``sel`` (ordre stable)."""
    import json

    src = await get_search(db, project, sel.from_search_id)
    if src.kind == "blind" and any(
        v is not None
        for v in (sel.status, sel.answers, sel.reviewed, sel.confidence_min, sel.confidence_max)
    ):  # sinon ces critères serviraient d'oracle sur les réponses masquées
        raise HTTPException(status_code=422, detail="criteria unavailable on a blind search")
    answers = json.dumps(sel.answers) if sel.answers else None
    where, _ps, pconf, _pd, annotated = article_filter(src, sel.status, answers)
    on_results = any(
        v is not None
        for v in (sel.status, sel.answers, sel.reviewed, sel.confidence_min, sel.confidence_max)
    )
    if on_results:  # critères sur les résultats (et le lot) de la recherche d'origine
        cond = list(where)
    elif sel.from_search_id:  # « même lot que cette recherche »
        cond = [lot_condition(src)]
    else:  # aucun critère : tout le corpus
        cond = [Article.project_id == project.id]
    if sel.reviewed is True:
        cond.append(annotated)
    elif sel.reviewed is False:
        cond.append(~annotated)
    if sel.has_fulltext is True:
        cond.append(Article.fulltext_source.is_not(None))
    elif sel.has_fulltext is False:
        cond.append(Article.fulltext_source.is_(None))
    if sel.confidence_min is not None:
        cond.append(pconf >= sel.confidence_min)
    if sel.confidence_max is not None:
        cond.append(pconf <= sel.confidence_max)
    if sel.ids:
        cond.append(Article.id.in_(sel.ids))
    ids = list((await db.execute(select(Article.id).where(*cond).order_by(Article.id))).scalars())
    if sel.sample_n and sel.sample_n < len(ids):
        ids = sorted(random.Random(sel.seed).sample(ids, sel.sample_n))
    return ids


async def _stats(db: AsyncSession, s: Search) -> dict:
    lot = (
        await db.execute(select(func.count()).select_from(Article).where(lot_condition(s)))
    ).scalar_one()
    predicted = (
        await db.execute(
            select(func.count(func.distinct(ArticlePrediction.article_id)))
            .join(Article, Article.id == ArticlePrediction.article_id)
            .where(Article.project_id == s.project_id, in_search(ArticlePrediction.search_id, s))
        )
    ).scalar_one()
    reviewed = (
        await db.execute(
            select(func.count(func.distinct(HumanAnnotation.article_id)))
            .join(Article, Article.id == HumanAnnotation.article_id)
            .where(Article.project_id == s.project_id, in_search(HumanAnnotation.search_id, s))
        )
    ).scalar_one()
    job = (
        (
            await db.execute(
                select(InferenceJob)
                .where(
                    InferenceJob.project_id == s.project_id, in_search(InferenceJob.search_id, s)
                )
                .order_by(InferenceJob.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return {
        "lot_size": lot,
        "predicted": predicted,
        "reviewed": reviewed,
        "last_job": {"status": job.status, "model": job.model_used} if job else None,
    }


def _payload(s: Search, stats: dict, parent: Search | None, author: User | None) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "kind": s.kind,
        "is_default": s.is_default,
        "question": s.question or DEFAULT_QUESTION,
        "fields": project_fields(s),
        "text_sections": s.text_sections,
        "parent": {"id": str(parent.id), "name": parent.name} if parent else None,
        "parent_spec": s.parent_spec,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "created_by": (author.full_name or author.email) if author else None,
        **stats,
    }


async def _full_payload(db: AsyncSession, s: Search) -> dict:
    parent = await db.get(Search, s.parent_id) if s.parent_id else None
    author = await db.get(User, s.created_by) if s.created_by else None
    return _payload(s, await _stats(db, s), parent, author)


@router.get("/{project_id}/searches")
async def list_searches(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    project = await _require_project_member(db, project_id, user)
    await default_search(db, project)
    await db.commit()
    rows = (
        (
            await db.execute(
                select(Search)
                .where(Search.project_id == project.id)
                .order_by(Search.is_default.desc(), Search.created_at, Search.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [await _full_payload(db, s) for s in rows]}


@router.post("/{project_id}/searches/preview")
async def preview_selection(
    project_id: uuid.UUID, sel: Selection, db: DbDep, user: UserDep
) -> dict:
    """Combien d'articles répondent à cette sélection (avant de créer la recherche)."""
    project = await _require_project_member(db, project_id, user)
    return {"count": len(await select_articles(db, project, sel))}


@router.post("/{project_id}/searches", status_code=201)
async def create_search(
    project_id: uuid.UUID, payload: SearchCreate, db: DbDep, user: UserDep
) -> dict:
    """Crée une recherche. Sa question et ses items par défaut sont ceux de la recherche dont on
    dérive le lot (``selection.from_search_id``), sinon ceux de la recherche initiale."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    sel = payload.selection
    origin = await get_search(db, project, sel.from_search_id)
    ids = await select_articles(db, project, sel)
    if not ids:
        raise HTTPException(status_code=422, detail="selection is empty")
    question = payload.question if payload.question is not None else origin.question
    fields = payload.fields if payload.fields is not None else origin.fields
    sections = payload.text_sections if payload.text_sections is not None else origin.text_sections
    s = Search(
        project_id=project.id,
        name=payload.name,
        question=(question or "").strip() or None,
        fields=fields,
        text_sections=sections,
        read_windows=payload.read_windows or origin.read_windows,
        kind=payload.kind,
        lot_all=False,
        parent_id=origin.id if not _is_all(sel) else None,
        parent_spec=None if _is_all(sel) else sel.model_dump(mode="json", exclude_none=True),
        created_by=user.id,
    )
    s.definition_hash = definition_hash(s.question, s.fields)
    db.add(s)
    await db.flush()
    db.add_all(SearchArticle(search_id=s.id, article_id=i) for i in ids)
    await db.commit()
    await db.refresh(s)
    return await _full_payload(db, s)


@router.get("/{project_id}/searches/{search_id}")
async def get_search_detail(
    project_id: uuid.UUID, search_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    project = await _require_project_member(db, project_id, user)
    return await _full_payload(db, await get_search(db, project, search_id))


@router.patch("/{project_id}/searches/{search_id}")
async def update_search(
    project_id: uuid.UUID, search_id: uuid.UUID, payload: SearchUpdate, db: DbDep, user: UserDep
) -> dict:
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    s = await get_search(db, project, search_id)
    if payload.name is not None:
        s.name = payload.name.strip() or s.name
    await db.commit()
    return await _full_payload(db, s)


@router.delete("/{project_id}/searches/{search_id}", status_code=204)
async def delete_search(
    project_id: uuid.UUID, search_id: uuid.UUID, db: DbDep, user: UserDep
) -> None:
    """Supprime la recherche avec ses résultats, relectures, analyses et références. La recherche
    initiale ne se supprime pas."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    s = await get_search(db, project, search_id)
    if s.is_default:
        raise HTTPException(status_code=409, detail="the initial search cannot be deleted")
    # Le gold est partagé par les recherches de même question : on le rattache à l'une d'elles
    # plutôt que de l'effacer avec cette recherche.
    heir = (
        await db.execute(
            select(Search.id)
            .where(
                Search.project_id == project.id,
                Search.definition_hash == s.definition_hash,
                Search.id != s.id,
            )
            .order_by(Search.is_default.desc(), Search.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if heir is not None:
        await db.execute(
            update(GoldArticle).where(GoldArticle.search_id == s.id).values(search_id=heir)
        )
    await db.delete(s)
    await db.commit()
