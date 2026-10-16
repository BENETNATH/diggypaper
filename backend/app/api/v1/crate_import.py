"""Importer un RO-Crate : le zip exporté par Diggypaper recrée un projet."""

import logging
import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crate_import as ci
from app.api.deps import get_current_user, get_db, rate_limit_user
from app.db.models import Project, ProjectMember, ProjectMemberRole, ProjectStatus, User
from app.tasks.crate_import import import_crate_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["crate-import"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

MAX_UPLOAD = 1024**3  # 1 Go (un crate avec beaucoup de PDF est gros)


def upload_dir() -> Path:
    d = Path(os.environ.get("EXPORT_DIR", "/app/exports")) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post(
    "/import-crate",
    status_code=202,
    dependencies=[Depends(rate_limit_user("import_crate", 5))],
)
async def import_crate(request: Request, db: DbDep, user: UserDep) -> dict:
    """Corps de la requête = le zip. Crée un projet (vous en êtes LEAD) et lance la restauration
    en tâche de fond ; la progression est dans GET /projects/{id}/jobs."""
    if not user.may_create_projects:
        raise HTTPException(status_code=403, detail="project creation not allowed")
    path = upload_dir() / f"crate-{uuid.uuid4().hex}.zip"
    size = 0
    try:
        with open(path, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="file too large")
                out.write(chunk)
        try:
            zf, info = ci.open_crate(path)
            zf.close()
            ci.project_definition(info)
        except ci.CrateError as exc:
            raise HTTPException(status_code=422, detail=f"invalid_crate:{exc}") from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    project = Project(
        title=str(info["title"]).strip()[:300],
        description=ci._clean(info.get("description"), 5000),
        created_by=user.id,
        status=ProjectStatus.PROCESSING,
    )
    db.add(project)
    await db.flush()
    db.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await db.commit()
    job = import_crate_task.delay(str(project.id), str(path), str(user.id))
    return {"project_id": str(project.id), "job_id": job.id}
