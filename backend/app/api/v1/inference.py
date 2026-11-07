"""Massive inference routes (MODULE 4, lot 5).

Read (status): member of the project. Trigger: LEAD of the project, rate limiting per
IP + "one PENDING/RUNNING job per project" guard + "FROZEN project" guard.

The status of the last job + the key statistics are exposed via
``GET /projects/{id}/inference`` (in-app notifications by polling).
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
    Article,
    ArticlePrediction,
    InferenceJob,
    InferenceJobStatus,
    Project,
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
    prompt_template_id: uuid.UUID | None = None  # default: prompt generated from the question
    model: str = Field(min_length=1)
    prefilter_model: str | None = None  # stage 1: small model on title + abstract, optional
    resume: bool = False  # resumes the last interrupted analysis (skips the articles already done)
    search_id: uuid.UUID | None = None  # analyzed search (default: the initial search)
    gold_examples: int = Field(default=0, ge=0, le=20)  # gold examples added to the prompt
    instructions: str | None = Field(  # instruction added to the prompt
        default=None, max_length=2000
    )
    # score the results against the gold (batch articles in the gold)
    validate_with_gold: bool = False


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
        "model_info": job.model_info,  # frozen snapshot (no secret by construction)
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
    """Triggers the massive inference (Celery task). 202 {job_id, status}."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    # Guard: the corpus must be FROZEN (reproducibility, spec MODULE 2/4).
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
    # The model is referenced by name (model_used): check that it is registered.
    # Only the models accessible to this LEAD in this project are launchable (resources).
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
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    result = inference_task.delay(str(job.id))
    status = result.state if celery_app.conf.task_always_eager else "PENDING"
    return {"job_id": str(job.id), "status": status}


class RerunCreate(BaseModel):
    model: str = Field(min_length=1)  # the model to re-run with (from the user's registry)


def _job_belongs_to_search(job: InferenceJob, scope) -> bool:
    """True if the job's search is the requested scope (NULL = the default search)."""
    if scope.is_default:
        return job.search_id in (scope.id, None)
    return job.search_id == scope.id


@router.post(
    "/{project_id}/searches/{search_id}/rerun",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("inference", INFERENCE_RATE_LIMIT))],
)
async def rerun_inference(
    project_id: uuid.UUID,
    search_id: uuid.UUID,
    payload: RerunCreate,
    db: DbDep,
    user: UserDep,
) -> dict:
    """Re-run the same search with another model (spec 2026-11-07 §2).

    Creates a NEW inference job on the same lot, inheriting the prompt + options
    of the last analysis; previous predictions are kept (each carries its job).
    """
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    if project.status not in (ProjectStatus.FROZEN, ProjectStatus.COMPLETED):
        raise HTTPException(status_code=409, detail="project corpus not frozen")
    scope = await get_search(db, project, search_id)
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
    if not await usable_models(db, user, [payload.model], project_id):
        raise HTTPException(status_code=422, detail="unknown model")
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
    if last is None:
        raise HTTPException(status_code=409, detail="no analysis to re-run")
    job = InferenceJob(
        project_id=project_id,
        search_id=scope.id,
        options=last.options,  # inherited (gold examples, validation)
        prompt_template_id=last.prompt_template_id,
        model_used=payload.model,
        status=InferenceJobStatus.PENDING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    result = inference_task.delay(str(job.id))
    status = result.state if celery_app.conf.task_always_eager else "PENDING"
    return {"job_id": str(job.id), "status": status}


def _model_info(job: InferenceJob) -> dict:
    main = (job.model_info or {}).get("main") or {}
    return {"name": job.model_used, "version": main.get("version")}


@router.get("/{project_id}/searches/{search_id}/comparison")
async def compare_jobs(
    project_id: uuid.UUID,
    search_id: uuid.UUID,
    job_a: uuid.UUID,
    job_b: uuid.UUID,
    db: DbDep,
    user: UserDep,
) -> dict:
    """Compares two inference jobs of the same search, article by article (spec §3).

    The denominator is the INTERSECTION of the two lots; articles predicted by
    only one job are excluded (counted in ``n_excluded``). Agreement is measured
    on the main field, normalized ("oui" == "include" == true).
    """
    from app.agreement import field_value, values_agree

    await _require_project_member(db, project_id, user)
    project = await db.get(Project, project_id)
    scope = await get_search(db, project, search_id)
    jobs = {}
    for jid in (job_a, job_b):
        job = await db.get(InferenceJob, jid)
        if job is None or job.project_id != project_id or not _job_belongs_to_search(job, scope):
            raise HTTPException(status_code=404, detail="job not in this search")
        jobs[jid] = job

    async def preds(job_id: uuid.UUID) -> dict[uuid.UUID, ArticlePrediction]:
        rows = (
            await db.execute(
                select(ArticlePrediction).where(ArticlePrediction.inference_job_id == job_id)
            )
        ).scalars().all()
        return {p.article_id: p for p in rows if p.article_id is not None}

    pred_a, pred_b = await preds(job_a), await preds(job_b)
    common = sorted(set(pred_a) & set(pred_b))
    titles: dict[str, str | None] = {}
    if common:
        rows = (
            await db.execute(select(Article.id, Article.title).where(Article.id.in_(common)))
        ).all()
        titles = {r.id: r.title for r in rows}
    rows_out, disagreements, n_agree = [], [], 0
    for aid in common:
        pa, pb = pred_a[aid], pred_b[aid]
        va = field_value(pa.decision_json, scope.fields)
        vb = field_value(pb.decision_json, scope.fields)
        agree = values_agree(va, vb)
        if agree:
            n_agree += 1
        row = {
            "article_id": aid,
            "title": titles.get(aid),
            "decision_a": va,
            "decision_b": vb,
            "confidence_a": pa.confidence_score,
            "confidence_b": pb.confidence_score,
            "agree": agree,
        }
        rows_out.append(row)
        if not agree:
            disagreements.append(
                {k: row[k] for k in ("article_id", "title", "decision_a", "decision_b")}
            )
    n = len(common)
    return {
        "n": n,
        "n_agree": n_agree,
        "agreement": round(n_agree / n, 4) if n else None,
        "model_a": _model_info(jobs[job_a]),
        "model_b": _model_info(jobs[job_b]),
        "n_excluded": len(pred_a) + len(pred_b) - 2 * n,
        "rows": rows_out,
        "disagreements": disagreements,
    }


@router.get("/{project_id}/searches/{search_id}/jobs")
async def list_search_jobs(
    project_id: uuid.UUID,
    search_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
) -> dict:
    """The inference jobs of a search, newest first (comparison selector, spec §4)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search_id)
    rows = (
        await db.execute(
            select(InferenceJob)
            .where(InferenceJob.project_id == project_id, in_search(InferenceJob.search_id, scope))
            .order_by(InferenceJob.created_at.desc(), InferenceJob.id.desc())
        )
    ).scalars().all()
    return {"jobs": [_job_payload(j) for j in rows]}


CANCELLED = "cancelled"  # job.error of a manually stopped analysis (status FAILED, resumable)


@router.post("/{project_id}/inference/cancel")
async def cancel_inference(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Stops the analysis in progress (or pending). Already-analyzed articles are kept;
    "Resume" continues later. The article being processed finishes first."""
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
    """Status of the last inference job of the search + statistics (in-app notifications).

    ``active``: the project's job in progress (all searches), else ``{"job": null}`` - one
    analysis at a time per project."""
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
