"""Project deletion (FK cascading rows + export zips) and account deletion.

Rule: an invited account (neither ADMIN nor project creator) with no project
left has no reason to exist (it would consume resources doing nothing); it is
deleted as soon as it loses its last membership. Creators and admins are never
deleted this way.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    ExportJob,
    HumanAnnotation,
    Invitation,
    Project,
    ProjectMember,
    User,
    UserRole,
)


async def delete_user(db: AsyncSession, user: User) -> None:
    """Deletes the account; its annotations remain, anonymized (hashed). Commit is the caller's."""
    anon = hashlib.sha256((get_settings().anon_salt + user.email).encode("utf-8")).hexdigest()
    await db.execute(
        update(HumanAnnotation)
        .where(HumanAnnotation.user_id == user.id)
        .values(user_id=None, annotator_hash=anon)
    )
    await db.execute(delete(Invitation).where(func.lower(Invitation.email) == user.email.lower()))
    await db.delete(user)


async def purge_orphans(
    db: AsyncSession, user_ids: list[uuid.UUID] | None = None, keep: tuple = ()
) -> int:
    """Deletes invited accounts with no project (among ``user_ids``, or all). Commit: caller."""
    q = select(User).where(
        User.role != UserRole.ADMIN,
        User.can_create_projects.is_not(True),
        User.id.not_in(select(ProjectMember.user_id)),
    )
    if user_ids is not None:
        q = q.where(User.id.in_(user_ids))
    if keep:
        q = q.where(User.id.not_in(keep))
    users = (await db.execute(q)).scalars().all()
    for u in users:
        await delete_user(db, u)
    return len(users)


async def delete_projects(db: AsyncSession, project_ids: list, keep: tuple = ()) -> int:
    """Deletes these projects: the ON DELETE CASCADE FKs empty articles, PDFs, jobs, shares…

    The export zips (outside the database) are erased here, and members left without
    a project are deleted (except ``keep``). Commit is the caller's responsibility.
    """
    if not project_ids:
        return 0
    members = (
        (
            await db.execute(
                select(ProjectMember.user_id).where(ProjectMember.project_id.in_(project_ids))
            )
        )
        .scalars()
        .all()
    )
    names = (
        (
            await db.execute(
                select(ExportJob.filename).where(
                    ExportJob.project_id.in_(project_ids), ExportJob.filename.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    directory = Path(os.environ.get("EXPORT_DIR", "/app/exports"))
    for name in names:
        (directory / name).unlink(missing_ok=True)
    res = await db.execute(delete(Project).where(Project.id.in_(project_ids)))
    await purge_orphans(db, list(members), keep)
    return res.rowcount or 0
