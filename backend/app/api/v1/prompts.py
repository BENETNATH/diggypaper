"""Routes Prompt Studio (prompt_templates) — Module 3.

Lecture : membre du projet. Écriture (création/édition) : LEAD du projet.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.projects import _require_project_member
from app.db.models import (
    ProjectMember,
    ProjectMemberRole,
    PromptStatus,
    PromptTemplate,
    PromptType,
    User,
    UserRole,
)
from app.schema import build_system_prompt, project_fields

router = APIRouter(prefix="/projects", tags=["prompts"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

_PROMPT_TYPES = {"INCLUSION", "EXTRACTION"}
_STATUSES = {"DRAFT", "TESTED", "PRODUCTION"}


class PromptCreate(BaseModel):
    version_number: str = Field(min_length=1, max_length=50)
    title: str | None = None
    system_prompt: str = Field(min_length=1)
    user_prompt_schema: str | None = None
    prompt_type: str = "EXTRACTION"

    @field_validator("prompt_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in _PROMPT_TYPES:
            raise ValueError(f"prompt_type must be one of {sorted(_PROMPT_TYPES)}")
        return v


class PromptUpdate(BaseModel):
    title: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    user_prompt_schema: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        return v


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    """LEAD du projet (ou ADMIN global)."""
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _payload(p: PromptTemplate) -> dict:
    return {
        "id": str(p.id),
        "project_id": str(p.project_id) if p.project_id else None,
        "version_number": p.version_number,
        "title": p.title,
        "system_prompt": p.system_prompt,
        "user_prompt_schema": p.user_prompt_schema,
        "prompt_type": p.prompt_type,
        "status": p.status,
    }


@router.post("/{project_id}/prompts", status_code=201)
async def create_prompt(
    project_id: uuid.UUID, payload: PromptCreate, db: DbDep, user: UserDep
) -> dict:
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    p = PromptTemplate(
        project_id=project_id,
        version_number=payload.version_number,
        title=payload.title,
        system_prompt=payload.system_prompt,
        user_prompt_schema=payload.user_prompt_schema,
        prompt_type=payload.prompt_type,
        status=PromptStatus.DRAFT,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _payload(p)


async def ensure_prompt(db: AsyncSession, project) -> PromptTemplate:
    """Prompt généré depuis la question + champs ; réutilise l'identique, sinon nouvelle version."""
    system = build_system_prompt(project.question, project_fields(project))
    user = "Article text:\n{article_text}"
    existing = (
        (await db.execute(select(PromptTemplate).where(PromptTemplate.project_id == project.id)))
        .scalars()
        .all()
    )
    for p in existing:
        if p.system_prompt == system and p.user_prompt_schema == user:
            return p
    p = PromptTemplate(
        project_id=project.id,
        version_number=f"v{len(existing) + 1}.0",
        title="Généré depuis la question du projet",
        system_prompt=system,
        user_prompt_schema=user,
        prompt_type=PromptType.EXTRACTION,
        status=PromptStatus.DRAFT,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@router.post("/{project_id}/prompts/generate", status_code=201)
async def generate_prompt(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Génère (ou retrouve) le prompt du projet depuis sa question et ses champs."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    return _payload(await ensure_prompt(db, project))


@router.get("/{project_id}/prompts")
async def list_prompts(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    items = (
        (
            await db.execute(
                select(PromptTemplate)
                .where(PromptTemplate.project_id == project_id)
                .order_by(PromptTemplate.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_payload(p) for p in items]}


@router.get("/{project_id}/prompts/{prompt_id}")
async def get_prompt(project_id: uuid.UUID, prompt_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    p = await db.get(PromptTemplate, prompt_id)
    if p is None or p.project_id != project_id:
        raise HTTPException(status_code=404, detail="prompt not found")
    return _payload(p)


@router.patch("/{project_id}/prompts/{prompt_id}")
async def update_prompt(
    project_id: uuid.UUID, prompt_id: uuid.UUID, payload: PromptUpdate, db: DbDep, user: UserDep
) -> dict:
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    p = await db.get(PromptTemplate, prompt_id)
    if p is None or p.project_id != project_id:
        raise HTTPException(status_code=404, detail="prompt not found")
    if payload.title is not None:
        p.title = payload.title
    if payload.system_prompt is not None:
        p.system_prompt = payload.system_prompt
    if payload.user_prompt_schema is not None:
        p.user_prompt_schema = payload.user_prompt_schema
    if payload.status is not None:
        p.status = payload.status
    await db.commit()
    await db.refresh(p)
    return _payload(p)
