"""Invitations d'inscription suivies en base : liste admin, renvoi, révocation."""

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
) -> Invitation:
    """Invitation en attente pour (e-mail, projet) : réutilisée si elle existe, sinon créée."""
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
    await db.flush()
    return inv


async def render_invitation(db: AsyncSession, inv: Invitation) -> tuple[str, str, str]:
    """(objet, corps, lien d'inscription) de l'e-mail, dans la langue de l'invité."""
    token = create_invite_token(
        inv.email,
        inv.language,
        str(inv.project_id) if inv.project_id else None,
        inv.role,
        str(inv.id),
    )
    link = f"{get_settings().app_base_url}/register?token={token}"
    if inv.project_id is None:
        return t(inv.language, "invite_subject"), t(inv.language, "invite_body", link=link), link
    project = await db.get(Project, inv.project_id)
    inviter = await db.get(User, inv.invited_by) if inv.invited_by else None
    fields = dict(
        project=project.title if project else "",
        inviter=(inviter.full_name or inviter.email) if inviter else "",
        role=inv.role or "ANNOTATOR",
        link=link,
    )
    return (
        t(inv.language, "project_invite_subject", project=fields["project"]),
        t(inv.language, "project_invite_body", **fields),
        link,
    )
