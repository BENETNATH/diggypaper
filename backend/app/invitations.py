"""Registration invitations tracked in the database: admin list, resend, revocation."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_invite_token
from app.db.models import Invitation, Project, User
from app.i18n import t


async def upsert_invitation(
    db: AsyncSession,
    email: str,
    language: str,
    invited_by: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    role: str | None = None,
    can_create_projects: bool = False,
) -> Invitation:
    """Pending invitation for (email, project): reused if it exists, else created."""
    inv = (
        (
            await db.execute(
                select(Invitation).where(
                    func.lower(Invitation.email) == email.lower(),
                    Invitation.accepted_at.is_(None),
                    Invitation.project_id.is_(project_id)
                    if project_id is None
                    else Invitation.project_id == project_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if inv is None:
        inv = Invitation(email=email, project_id=project_id, role=role)
        db.add(inv)
    inv.language, inv.role, inv.invited_by = language, role, invited_by
    inv.can_create_projects = can_create_projects
    await db.flush()
    return inv


async def render_invitation(db: AsyncSession, inv: Invitation) -> tuple[str, str, str]:
    """(subject, body, registration link) of the email, in the invitee's language."""
    token = create_invite_token(
        inv.email,
        inv.language,
        str(inv.project_id) if inv.project_id else None,
        inv.role,
        str(inv.id),
    )
    link = f"{get_settings().app_base_url}/register?token={token}"
    if inv.project_id is None:
        return (
            t(inv.language, "invite_subject"),
            t(inv.language, "invite_body", link=link, token=token),
            link,
        )
    project = await db.get(Project, inv.project_id)
    inviter = await db.get(User, inv.invited_by) if inv.invited_by else None
    fields = dict(
        project=project.title if project else "",
        inviter=(inviter.full_name or inviter.email) if inviter else "",
        role=inv.role or "ANNOTATOR",
        link=link,
        token=token,
    )
    return (
        t(inv.language, "project_invite_subject", project=fields["project"]),
        t(inv.language, "project_invite_body", **fields),
        link,
    )
