"""Routes inférence massive (MODULE 4, lot 5).

Lecture (statut) : membre du projet. Trigger : LEAD du projet, rate limiting par
IP + garde « un job PENDING/RUNNING par projet » + garde « projet FROZEN ».

Le statut du dernier job + les statistiques clés sont exposés via
``GET /projects/{id}/inference`` (notifications in-app par polling).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import _require_project_member
from app.api.v1.prompts import ensure_prompt
from app.celery_app import celery_app
from app.db.models import (
    InferenceJob,
    InferenceJobStatus,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    PromptTemplate,
    User,
    UserRole,
)
from app.model_access import usable_models
from app.searches import get_search, in_search
from app.tasks.inference import inference_task

router = APIRouter(prefix="/projects", tags=["inference"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

INFERENCE_RATE_LIMIT = 5


class InferenceCreate(BaseModel):
    prompt_template_id: uuid.UUID | None = None  # défaut : prompt généré depuis la question
    model: str = Field(min_length=1)
    prefilter_model: str | None = None  # étage 1 : petit modèle sur titre + abstract, optionnel
    resume: bool = False  # reprend la dernière analyse interrompue (saute les articles déjà faits)
    search_id: uuid.UUID | None = None  # recherche analysée (défaut : la recherche initiale)
    gold_examples: int = Field(default=0, ge=0, le=20)  # exemples du gold ajoutés au prompt
    instructions: str | None = Field(default=None, max_length=2000)  # consigne ajoutée au prompt
    validate_with_gold: bool = False  # noter les résultats contre le gold (articles du lot au gold)


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _job_payload(job: InferenceJob) -> dict:
    return {
        "id": str(job.id),
        "project_id": str(job.project_id) if job.project_id else None,
        "prompt_template_id": str(job.prompt_template_id) if job.prompt_template_id else None,
        "model_used": job.model_used,
        "prefilter_model": job.prefilter_model,
        "search_id": str(job.search_id) if job.search_id else None,
        "status": job.status,
        "result": job.result_json,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post(
    "/{project_id}/inference",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("inference", INFERENCE_RATE_LIMIT))],
)
async def trigger_inference(
    project_id: uuid.UUID, payload: InferenceCreate, db: DbDep, user: UserDep
) -> dict:
    """Trigger l'inférence massive (task Celery). 202 {job_id, status}."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    # Garde : le corpus doit être FROZEN (reproductibilité, spec MODULE 2/4).
    if project.status not in (ProjectStatus.FROZEN, ProjectStatus.COMPLETED):
        raise HTTPException(status_code=409, detail="project corpus not frozen")
    scope = await get_search(db, project, payload.search_id)
    if scope.schedule_state == "waiting":
        raise HTTPException(status_code=409, detail="search is scheduled: it starts by itself")
    if payload.prompt_template_id is None:
        prompt = await ensure_prompt(db, project, scope, payload.instructions)
    else:
        prompt = await db.get(PromptTemplate, payload.prompt_template_id)
        if prompt is None or prompt.project_id != project_id:
            raise HTTPException(status_code=422, detail="prompt template not in this project")
    # Le modèle est référencé par nom (model_used) : vérifie qu'il est enregistré.
    # Seuls les modèles accessibles à ce LEAD dans ce projet sont lançables (ressources).
    if not await usable_models(db, user, [payload.model], project_id):
        raise HTTPException(status_code=422, detail="unknown model")
    if payload.prefilter_model and not await usable_models(
        db, user, [payload.prefilter_model], project_id
    ):
        raise HTTPException(status_code=422, detail="unknown prefilter_model")
    active = (
        await db.execute(
            select(InferenceJob.id)
            .where(
                InferenceJob.project_id == project_id,
                InferenceJob.status.in_([InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="inference already in progress")
    resume_after = None
    if payload.resume:
        last = (
            (
                await db.execute(
                    select(InferenceJob)
                    .where(
                        InferenceJob.project_id == project_id,
                        in_search(InferenceJob.search_id, scope),
                    )
                    .order_by(InferenceJob.created_at.desc(), InferenceJob.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if last is None or last.status != InferenceJobStatus.FAILED:
            raise HTTPException(status_code=409, detail="no interrupted analysis to resume")
        if last.prompt_template_id != prompt.id:
            raise HTTPException(status_code=409, detail="question changed: cannot resume")
        resume_after = last.resume_after or last.created_at
    job = InferenceJob(
        project_id=project_id,
        search_id=scope.id,
        options={"gold_examples": payload.gold_examples, "validate": payload.validate_with_gold},
        resume_after=resume_after,
        prompt_template_id=prompt.id,
        model_used=payload.model,
        prefilter_model=payload.prefilter_model,
        status=InferenceJobStatus.PENDING,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    result = inference_task.delay(str(job.id))
    status = result.state if celery_app.conf.task_always_eager else "PENDING"
    return {"job_id": str(job.id), "status": status}


CANCELLED = "cancelled"  # job.error d'une analyse arrêtée à la main (statut FAILED → reprenable)


@router.post("/{project_id}/inference/cancel")
async def cancel_inference(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Arrête l'analyse en cours (ou en attente). Les articles déjà analysés sont conservés ;
    « Reprendre » continue plus tard. L'article en cours de traitement se termine d'abord."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    job = (
        (
            await db.execute(
                select(InferenceJob)
                .where(
                    InferenceJob.project_id == project_id,
                    InferenceJob.status.in_(
                        [InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING]
                    ),
                )
                .order_by(InferenceJob.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if job is None:
        raise HTTPException(status_code=409, detail="no analysis in progress")
    job.status = InferenceJobStatus.FAILED
    job.error = CANCELLED
    job.completed_at = datetime.now(UTC)
    await db.commit()
    return _job_payload(job)


@router.get("/{project_id}/inference")
async def get_inference(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
    active: bool = False,
) -> dict:
    """Statut du dernier job d'inférence de la recherche + statistiques (notifications in-app).

    ``active`` : le job en cours du projet (toutes recherches), sinon ``{"job": null}`` — une seule
    analyse à la fois par projet."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    where = (
        [
            InferenceJob.project_id == project_id,
            InferenceJob.status.in_([InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING]),
        ]
        if active
        else [InferenceJob.project_id == project_id, in_search(InferenceJob.search_id, scope)]
    )
    job = (
        (
            await db.execute(
                select(InferenceJob)
                .where(*where)
                .order_by(InferenceJob.created_at.desc(), InferenceJob.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if job is None:
        return {"job": None}
    return {"job": _job_payload(job)}
