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
from app.api.v1.projects import _require_project_member
from app.db.models import (
    Article,
    GoldArticle,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.schema import project_fields, validate_decision

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


def _payload(g: GoldArticle, title: str | None = None) -> dict:
    return {
        "id": str(g.id),
        "project_id": str(g.project_id) if g.project_id else None,
        "article_id": str(g.article_id) if g.article_id else None,
        "article_title": title,
        "ground_truth_json": g.ground_truth_json,
    }


@router.post("/{project_id}/gold", status_code=201)
async def create_gold(project_id: uuid.UUID, payload: GoldCreate, db: DbDep, user: UserDep) -> dict:
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    _validate_ground_truth(payload.ground_truth_json, project)
    article = await db.get(Article, payload.article_id)
    if article is None or article.project_id != project_id:
        raise HTTPException(status_code=422, detail="article not in this project")
    g = GoldArticle(
        project_id=project_id,
        article_id=payload.article_id,
        ground_truth_json=payload.ground_truth_json,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return _payload(g, title=article.title)


@router.get("/{project_id}/gold")
async def list_gold(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    rows = (
        await db.execute(
            select(GoldArticle, Article.title)
            .join(Article, GoldArticle.article_id == Article.id)
            .where(GoldArticle.project_id == project_id)
            .order_by(GoldArticle.id)
        )
    ).all()
    return {"items": [_payload(g, title=title) for g, title in rows]}


@router.delete("/{project_id}/gold/{gold_id}", status_code=204)
async def delete_gold(project_id: uuid.UUID, gold_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    g = await db.get(GoldArticle, gold_id)
    if g is None or g.project_id != project_id:
        raise HTTPException(status_code=404, detail="gold article not found")
    await db.delete(g)
    await db.commit()
