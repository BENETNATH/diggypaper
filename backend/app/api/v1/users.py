"""Routes RGPD : anonymisation (DELETE /users/me/anonymize) + export (GET /users/me/export-data)."""

import hashlib
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_or_anonymized, get_db
from app.core.config import get_settings
from app.db.models import HumanAnnotation, ProjectMember, User, UserRole

router = APIRouter(prefix="/users", tags=["users"])


class ExportUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    language_preference: str
    data_consent_given: bool
    created_at: datetime


class ExportMembership(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    role_in_project: str


class ExportAnnotation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    article_id: uuid.UUID | None = None
    decision_json: dict | None = None
    notes: str | None = None
    created_at: datetime


class ExportData(BaseModel):
    user: ExportUser
    memberships: list[ExportMembership]
    annotations: list[ExportAnnotation]


@router.delete("/me/anonymize", status_code=204)
async def anonymize(
    user: Annotated[User, Depends(get_current_user_or_anonymized)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Anonymizes the user in a transaction: PII erased, annotations kept via hash.

    Idempotent (F2): if already anonymized, returns 204 without changing anything.
    """
    if user.anonymized_hash is not None:
        # Already anonymized -> idempotent, we touch nothing.
        return Response(status_code=204)
    settings = get_settings()
    anonymized_hash = hashlib.sha256((settings.anon_salt + user.email).encode("utf-8")).hexdigest()
    await db.execute(
        update(HumanAnnotation)
        .where(HumanAnnotation.user_id == user.id)
        .values(user_id=None, annotator_hash=anonymized_hash)
    )
    user.email = f"anonymized-{uuid.uuid4().hex[:12]}@invalid.local"  # m5 : 12 hex
    user.hashed_password = ""
    user.full_name = None
    user.role = UserRole.RESEARCHER  # F2 : dégradation (plus d'accès admin résiduel)
    user.anonymized_hash = anonymized_hash
    await db.commit()
    return Response(status_code=204)


@router.get("/me/export-data")
async def export_data(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExportData:
    """JSON export of the account's data (without hashed_password or sensitive fields)."""
    memberships = (
        (await db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id)))
        .scalars()
        .all()
    )
    annotations = (
        (await db.execute(select(HumanAnnotation).where(HumanAnnotation.user_id == user.id)))
        .scalars()
        .all()
    )
    return ExportData(
        user=ExportUser.model_validate(user),
        memberships=[ExportMembership.model_validate(m) for m in memberships],
        annotations=[ExportAnnotation.model_validate(a) for a in annotations],
    )
