"""Suppression complète de projets : lignes (cascade FK) ET fichiers d'export sur disque."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExportJob, Project


async def delete_projects(db: AsyncSession, project_ids: list) -> int:
    """Supprime ces projets : les FK ON DELETE CASCADE vident articles, PDF, jobs, partages…

    Les zips d'export (hors base) sont effacés ici. Commit à la charge de l'appelant.
    """
    if not project_ids:
        return 0
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
    return res.rowcount or 0
