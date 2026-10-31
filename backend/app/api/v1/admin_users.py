"""Account administration: listing, modification, deactivation, deletion (with their
projects and leftovers), pending invitations (resend / revocation), all projects.

Reserved for global ADMINs.
"""

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_db
from app.cleanup import delete_projects, purge_orphans
from app.cleanup import delete_user as _delete_user
from app.db.models import (
    Article,
    HumanAnnotation,
    Invitation,
    LanguageCode,
    ModelRegistry,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.emails import mailer_from_db
from app.invitations import render_invitation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[User, Depends(get_current_admin)]


class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Literal["ADMIN", "RESEARCHER"] | None = None
    language_preference: Literal["fr", "en", "de", "es"] | None = None
    is_active: bool | None = None
    can_create_projects: bool | None = None


async def _other_active_admins(db: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.role == UserRole.ADMIN,
                User.is_active.is_not(False),
                User.anonymized_hash.is_(None),
                User.id != user_id,
            )
        )
    ).scalar_one()


async def _counts(db: AsyncSession, col) -> dict:
    rows = await db.execute(select(col, func.count()).where(col.is_not(None)).group_by(col))
    return {k: n for k, n in rows}


@router.get("/users")
async def list_users(db: DbDep, admin: AdminDep) -> dict:
    users = (await db.execute(select(User).order_by(User.created_at, User.email))).scalars().all()
    member_of = await _counts(db, ProjectMember.user_id)
    created = await _counts(db, Project.created_by)
    models = await _counts(db, ModelRegistry.owner_id)
    annotations = await _counts(db, HumanAnnotation.user_id)
    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role.value if u.role else "RESEARCHER",
                "language_preference": u.language_preference.value
                if u.language_preference
                else None,
                "is_active": u.is_active is not False,
                "can_create_projects": u.may_create_projects,
                "anonymized": u.anonymized_hash is not None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "projects": member_of.get(u.id, 0),
                "created_projects": created.get(u.id, 0),
                "models": models.get(u.id, 0),
                "annotations": annotations.get(u.id, 0),
            }
            for u in users
        ]
    }


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, payload: UserPatch, db: DbDep, admin: AdminDep) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    demoting = payload.role == "RESEARCHER" and user.role == UserRole.ADMIN
    disabling = payload.is_active is False and user.is_active is not False
    if (demoting or disabling) and user.role == UserRole.ADMIN:
        if user.id == admin.id or await _other_active_admins(db, user.id) == 0:
            raise HTTPException(status_code=409, detail="cannot remove the last active admin")
    if disabling and user.id == admin.id:
        raise HTTPException(status_code=409, detail="cannot disable yourself")
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or None
    if payload.role is not None:
        user.role = UserRole(payload.role)
    if payload.language_preference is not None:
        user.language_preference = LanguageCode(payload.language_preference)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.can_create_projects is not None:
        user.can_create_projects = payload.can_create_projects
    await db.commit()
    return {"id": str(user.id), "is_active": user.is_active, "role": user.role.value}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    db: DbDep,
    admin: AdminDep,
    delete_their_projects: Annotated[bool, Query()] = False,
) -> dict:
    """Deletes the account and everything that is its own.

    Goes with the account: memberships, models it owns (and their shares), shares
    received, personal settings, pending invitations to its address. Its annotations
    stay, anonymized (hash). ``delete_their_projects``: also deletes the projects it
    created or is the sole LEAD of (articles, PDFs, analyses, on-disk exports included);
    otherwise those projects stay (visible to the admin) and are listed in the response.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="cannot delete yourself")
    if user.role == UserRole.ADMIN and await _other_active_admins(db, user.id) == 0:
        raise HTTPException(status_code=409, detail="cannot remove the last active admin")

    lead_of = select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id, ProjectMember.role_in_project == ProjectMemberRole.LEAD
    )
    other_leads = (
        select(ProjectMember.project_id)
        .where(
            ProjectMember.role_in_project == ProjectMemberRole.LEAD,
            ProjectMember.user_id != user.id,
        )
        .distinct()
    )
    theirs = (
        await db.execute(
            select(Project.id, Project.title).where(
                (Project.created_by == user.id)
                | (Project.id.in_(lead_of) & Project.id.not_in(other_leads))
            )
        )
    ).all()
    removed = 0
    if delete_their_projects:
        removed = await delete_projects(db, [p.id for p in theirs], keep=(user.id,))
    await _delete_user(db, user)
    await db.commit()
    return {
        "deleted_projects": removed,
        "orphaned_projects": [] if delete_their_projects else [p.title for p in theirs],
    }


@router.post("/users/purge-orphans")
async def purge_orphan_users(db: DbDep, admin: AdminDep) -> dict:
    """Deletes all guest accounts (non-creators) that belong to no project."""
    n = await purge_orphans(db)
    await db.commit()
    return {"deleted": n}


# --- Invitations en attente --------------------------------------------------------------


def _pending():
    return select(Invitation).where(
        Invitation.accepted_at.is_(None),
        func.lower(Invitation.email).not_in(select(func.lower(User.email))),
    )


@router.get("/invitations")
async def list_invitations(db: DbDep, admin: AdminDep) -> dict:
    rows = (
        await db.execute(
            _pending()
            .add_columns(Project.title, User.email)
            .join(Project, Project.id == Invitation.project_id, isouter=True)
            .join(User, User.id == Invitation.invited_by, isouter=True)
            .order_by(Invitation.created_at.desc())
        )
    ).all()
    return {
        "items": [
            {
                "id": str(i.id),
                "email": i.email,
                "language": i.language,
                "project": title,
                "role": i.role,
                "invited_by": by,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i, title, by in rows
        ]
    }


@router.post("/invitations/{invitation_id}/resend")
async def resend_invitation(invitation_id: uuid.UUID, db: DbDep, admin: AdminDep) -> dict:
    """Resends the e-mail (new link valid 7 d); without SMTP, returns the link to transmit."""
    inv = await db.get(Invitation, invitation_id)
    if inv is None or inv.accepted_at is not None:
        raise HTTPException(status_code=404, detail="invitation not found")
    subject, body, link = await render_invitation(db, inv)
    mailer = await mailer_from_db(db)
    sent = False
    if mailer is not None:
        try:
            await mailer.send(to=inv.email, subject=subject, body=body)
            sent = True
        except Exception:  # noqa: BLE001 - SMTP en panne : on renvoie le lien à la place
            logger.exception("invitation resend failed")
    return {"email_sent": sent, "link": None if sent else link}


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    """Revokes: the link already sent stops working."""
    res = await db.execute(delete(Invitation).where(Invitation.id == invitation_id))
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="invitation not found")
    await db.commit()


# --- All projects --------------------------------------------------------------------


@router.get("/projects")
async def list_all_projects(db: DbDep, admin: AdminDep) -> dict:
    members = await _counts(db, ProjectMember.project_id)
    articles = await _counts(db, Article.project_id)
    rows = (
        await db.execute(
            select(Project, User.email)
            .join(User, User.id == Project.created_by, isouter=True)
            .order_by(Project.created_at.desc())
        )
    ).all()
    return {
        "items": [
            {
                "id": str(p.id),
                "title": p.title,
                "created_by": email,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "members": members.get(p.id, 0),
                "articles": articles.get(p.id, 0),
            }
            for p, email in rows
        ]
    }
