"""Team of a project: listing of the members, invitation by e-mail, removal.

Inviting an unknown address sends a registration link (JWT 7 d carrying the project
and the role); an already registered address is added directly. If the e-mail does not go out
(SMTP absent/down), the link is returned for the LEAD to transmit it himself.
"""

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, rate_limit_user
from app.api.v1.projects import _require_project_member
from app.cleanup import purge_orphans
from app.core.config import get_settings
from app.db.models import Project, ProjectMember, ProjectMemberRole, User, UserRole
from app.emails import mailer_from_db
from app.i18n import default_lang, t
from app.invitations import render_invitation, upsert_invitation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["team"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


class MemberInvite(BaseModel):
    email: EmailStr
    role: Literal["LEAD", "ANNOTATOR"] = "ANNOTATOR"
    language: Literal["fr", "en", "de", "es"] | None = None


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    project = await _require_project_member(db, project_id, user)
    if user.role != UserRole.ADMIN:
        member = await db.get(ProjectMember, (project_id, user.id))
        if member is None or member.role_in_project != ProjectMemberRole.LEAD:
            raise HTTPException(status_code=403, detail="lead required")
    return project


@router.get("/{project_id}/members")
async def list_members(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    rows = (
        await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(User.email)
        )
    ).all()
    return {
        "items": [
            {
                "user_id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role": m.role_in_project.value if m.role_in_project else "ANNOTATOR",
            }
            for m, u in rows
        ]
    }


@router.post(
    "/{project_id}/members/invite",
    status_code=201,
    dependencies=[Depends(rate_limit_user("team_invite", 20))],
)
async def invite_member(
    project_id: uuid.UUID, payload: MemberInvite, db: DbDep, user: UserDep
) -> dict:
    project = await _require_lead(db, project_id, user)
    settings = get_settings()
    inviter = user.full_name or user.email
    existing = (
        await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        if await db.get(ProjectMember, (project_id, existing.id)) is not None:
            raise HTTPException(status_code=409, detail="already a member")
        db.add(
            ProjectMember(
                project_id=project_id,
                user_id=existing.id,
                role_in_project=ProjectMemberRole(payload.role),
            )
        )
        await db.commit()
        # Same response whether the account exists or not (no enumeration of accounts).
        status, lang = "invited", existing.language_preference.value
        link = f"{settings.app_base_url}/projects/{project_id}"
        kind = "project_added"
        is_new = False
    else:
        lang = payload.language or default_lang()
        inv = await upsert_invitation(
            db, payload.email, lang, user.id, project_id=project_id, role=payload.role
        )
        status, kind = "invited", "project_invite"
        is_new = True
        invite_subject, invite_body, link = await render_invitation(db, inv)
        await db.commit()
    mailer = await mailer_from_db(db)
    sent = False
    if mailer is not None:
        if is_new:
            subject, body = invite_subject, invite_body
        else:
            fields = dict(project=project.title, inviter=inviter, role=payload.role, link=link)
            subject = t(lang, f"{kind}_subject", project=project.title)
            body = t(lang, f"{kind}_body", **fields)
        try:
            await mailer.send(to=payload.email, subject=subject, body=body)
            sent = True
        except Exception:  # noqa: BLE001 - SMTP en panne : on renvoie le lien à la place
            logger.exception("project invitation e-mail failed")
    return {"status": status, "email_sent": sent, "link": link if is_new and not sent else None}


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: uuid.UUID, user_id: uuid.UUID, db: DbDep, user: UserDep
) -> None:
    await _require_lead(db, project_id, user)
    member = await db.get(ProjectMember, (project_id, user_id))
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    if member.role_in_project == ProjectMemberRole.LEAD:
        leads = (
            await db.execute(
                select(func.count())
                .select_from(ProjectMember)
                .where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.role_in_project == ProjectMemberRole.LEAD,
                )
            )
        ).scalar_one()
        if leads <= 1:
            raise HTTPException(status_code=409, detail="cannot remove the last lead")
    await db.delete(member)
    await db.flush()
    await purge_orphans(db, [user_id])  # plus aucun projet : le compte disparaît
    await db.commit()
