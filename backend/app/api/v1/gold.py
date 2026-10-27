"""Routes Gold Dataset (gold_articles) — Module 3.

Lecture : membre du projet. Écriture : LEAD du projet.
``ground_truth_json`` doit contenir les clés du schéma spec §5.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.projects import _require_project_member, latest_reviews
from app.db.models import (
    Article,
    GoldArticle,
    HumanAnnotation,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.schema import project_fields, validate_decision
from app.searches import get_search, gold_condition, in_search

router = APIRouter(prefix="/projects", tags=["gold"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# Clés obligatoires du schéma d'annotation (spec §5).
_GROUND_TRUTH_KEYS = {
    "use_ai_behavior",
    "animal_sex",
    "confidence_score",
    "evidence_quotes",
    "reasoning_summary",
}


class GoldCreate(BaseModel):
    article_id: uuid.UUID
    ground_truth_json: dict = Field(min_length=1)


class GoldFromReviews(BaseModel):
    """Ajoute au gold les relectures humaines (dernière relecture de chaque article)."""

    article_ids: list[uuid.UUID] | None = None  # None : tous les articles relus de la recherche
    refresh: bool = False  # remplace la copie des articles déjà au gold par la relecture actuelle


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _validate_ground_truth(gt: dict, project=None) -> None:
    if project is not None and project.fields is not None:
        errors = validate_decision(project_fields(project), gt)
        if errors:
            raise HTTPException(status_code=422, detail=f"ground_truth_json: {errors}")
        return
    missing = _GROUND_TRUTH_KEYS - gt.keys()
    if missing:
        raise HTTPException(
            status_code=422, detail=f"ground_truth_json missing keys: {sorted(missing)}"
        )


def _snapshot(ann: HumanAnnotation, fields: list[dict]) -> dict:
    """Copie figée d'une relecture : réponses aux items + pertinence, preuves, raisonnement."""
    d = ann.decision_json or {}
    out = {f["key"]: d.get(f["key"]) for f in fields}
    out["confidence_score"] = d.get("confidence_score", 1.0)
    out["evidence_quotes"] = d.get("evidence_quotes") or []
    out["reasoning_summary"] = d.get("reasoning_summary") or ""
    return out


def _answers_of(gt: dict | None, fields: list[dict]) -> dict:
    return {f["key"]: (gt or {}).get(f["key"]) for f in fields}


def _payload(
    g: GoldArticle,
    title: str | None,
    fields: list[dict],
    ann: HumanAnnotation | None,
    names: dict,
    latest: HumanAnnotation | None,
) -> dict:
    """Une ligne du gold : réponses, lien vers sa fiche source, relecteur, obsolescence."""
    d = (ann.decision_json or {}) if ann else {}
    stale = bool(
        latest is not None
        and ann is not None
        and latest.id != ann.id
        and _answers_of(latest.decision_json, fields) != _answers_of(g.ground_truth_json, fields)
    )
    return {
        "id": str(g.id),
        "project_id": str(g.project_id) if g.project_id else None,
        "article_id": str(g.article_id) if g.article_id else None,
        "article_title": title,
        "ground_truth_json": g.ground_truth_json,
        "search_id": str(g.search_id) if g.search_id else None,
        "source": {
            "annotation_id": str(ann.id),
            "by": names.get(ann.user_id),
            "at": ann.created_at.isoformat() if ann.created_at else None,
            "status": d.get("review_status"),
            "blind": bool(ann.blind),
        }
        if ann
        else None,
        "added_by": names.get(g.added_by),
        "stale": stale,  # la relecture a changé depuis la copie : « mettre à jour »
    }


async def _names(db: AsyncSession, user_ids: set) -> dict:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = await db.execute(select(User).where(User.id.in_(ids)))
    return {u.id: u.full_name or u.email for u in rows.scalars()}


async def _items(db: AsyncSession, project, scope, rows) -> list[dict]:
    """Lignes du gold + relecture source, relecteurs, dernière relecture (pour « obsolète »)."""
    fields = project_fields(scope)
    anns = {}
    ann_ids = [g.source_annotation_id for g, _ in rows if g.source_annotation_id]
    if ann_ids:
        for a in (
            await db.execute(select(HumanAnnotation).where(HumanAnnotation.id.in_(ann_ids)))
        ).scalars():
            anns[a.id] = a
    latest = await latest_reviews(db, [g.article_id for g, _ in rows if g.article_id], scope)
    names = await _names(
        db,
        {a.user_id for a in anns.values()} | {g.added_by for g, _ in rows},
    )
    return [
        _payload(
            g,
            title,
            fields,
            anns.get(g.source_annotation_id),
            names,
            (latest.get(g.article_id) or (None, None))[0],
        )
        for g, title in rows
    ]


@router.post("/{project_id}/gold", status_code=201)
async def create_gold(
    project_id: uuid.UUID,
    payload: GoldCreate,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Saisie manuelle d'une référence (LEAD). Préférer « Ajouter au gold » depuis une relecture."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    scope = await get_search(db, project, search)
    _validate_ground_truth(payload.ground_truth_json, scope)
    article = await db.get(Article, payload.article_id)
    if article is None or article.project_id != project_id:
        raise HTTPException(status_code=422, detail="article not in this project")
    dup = await db.execute(
        select(GoldArticle.id)
        .where(await gold_condition(db, project, scope), GoldArticle.article_id == article.id)
        .limit(1)
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="already in the gold standard")
    g = GoldArticle(
        project_id=project_id,
        search_id=scope.id,
        article_id=payload.article_id,
        ground_truth_json=payload.ground_truth_json,
        added_by=user.id,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return (await _items(db, project, scope, [(g, article.title)]))[0]


@router.post("/{project_id}/gold/from-reviews", status_code=201)
async def gold_from_reviews(
    project_id: uuid.UUID,
    payload: GoldFromReviews,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Ajoute au gold standard la dernière relecture humaine des articles (tout membre).

    La référence est une copie figée des réponses du relecteur, avec un lien vers sa fiche. Un
    article déjà au gold n'est pas dupliqué (``refresh`` : on met sa copie à jour)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    fields = project_fields(scope)
    if payload.article_ids:
        # Seulement les articles de CE projet : ``latest_reviews`` ne joint pas Article, et la
        # recherche initiale retient aussi les relectures legacy (``search_id`` NULL) de tout
        # projet.
        wanted = set(payload.article_ids)
        own = set(
            (
                await db.execute(
                    select(Article.id).where(
                        Article.project_id == project.id, Article.id.in_(wanted)
                    )
                )
            ).scalars()
        )
        if own != wanted:
            raise HTTPException(status_code=422, detail="article not in this project")
        reviews = await latest_reviews(db, list(wanted), scope)
    else:
        reviews = await latest_reviews(db, await _reviewed_ids(db, scope), scope)
        wanted = set(reviews)
    existing = {
        g.article_id: g
        for g in (
            await db.execute(select(GoldArticle).where(await gold_condition(db, project, scope)))
        ).scalars()
    }
    added = updated = skipped = 0
    is_lead = user.role == UserRole.ADMIN or bool(
        (m := await db.get(ProjectMember, (project_id, user.id)))
        and m.role_in_project == ProjectMemberRole.LEAD
    )
    for article_id in wanted:
        review = reviews.get(article_id)
        if review is None:
            skipped += 1
            continue
        ann = review[0]
        g = existing.get(article_id)
        if g is None:
            db.add(
                GoldArticle(
                    project_id=project_id,
                    search_id=scope.id,
                    article_id=article_id,
                    ground_truth_json=_snapshot(ann, fields),
                    source_annotation_id=ann.id,
                    added_by=user.id,
                )
            )
            added += 1
        elif payload.refresh and (is_lead or g.added_by == user.id):
            # pas de réécriture de la référence d'un autre (sauf LEAD) : saisie manuelle, relecteur
            g.ground_truth_json, g.source_annotation_id = _snapshot(ann, fields), ann.id
            updated += 1
        else:
            skipped += 1
    await db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


async def _reviewed_ids(db: AsyncSession, scope) -> list:
    rows = await db.execute(
        select(HumanAnnotation.article_id)
        .join(Article, Article.id == HumanAnnotation.article_id)
        .where(Article.project_id == scope.project_id, in_search(HumanAnnotation.search_id, scope))
        .distinct()
    )
    return [r for r in rows.scalars() if r]


@router.get("/{project_id}/gold")
async def list_gold(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict:
    """Références valables pour la recherche (celles de toute recherche de même question)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    rows = (
        await db.execute(
            select(GoldArticle, Article.title)
            .join(Article, GoldArticle.article_id == Article.id)
            .where(await gold_condition(db, project, scope))
            .order_by(GoldArticle.id)
        )
    ).all()
    return {"items": await _items(db, project, scope, rows)}


@router.delete("/{project_id}/gold/{gold_id}", status_code=204)
async def delete_gold(project_id: uuid.UUID, gold_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    """Retire une référence du gold (LEAD, ou la personne qui l'avait ajoutée)."""
    await _require_project_member(db, project_id, user)
    g = await db.get(GoldArticle, gold_id)
    if g is None or g.project_id != project_id:
        raise HTTPException(status_code=404, detail="gold article not found")
    if g.added_by != user.id:
        await _require_lead(db, project_id, user)
    await db.delete(g)
    await db.commit()
