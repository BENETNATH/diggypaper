"""Routes benchmark (Arena) — Module 3.

Lecture : membre du projet. Trigger : LEAD du projet, rate limiting par IP +
garde « un benchmark PENDING/RUNNING par projet ».
"""

import uuid
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
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ProjectMember,
    ProjectMemberRole,
    PromptTemplate,
    User,
    UserRole,
)
from app.model_access import usable_models
from app.tasks.benchmark import benchmark_task

router = APIRouter(prefix="/projects", tags=["benchmarks"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

BENCHMARK_RATE_LIMIT = 5


class BenchmarkCreate(BaseModel):
    prompt_template_id: uuid.UUID | None = None  # défaut : prompt généré depuis la question
    models: list[str] = Field(min_length=1)


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _run_payload(run: BenchmarkRun, results: list[dict] | None = None) -> dict:
    payload = {
        "id": str(run.id),
        "project_id": str(run.project_id) if run.project_id else None,
        "prompt_template_id": str(run.prompt_template_id) if run.prompt_template_id else None,
        "models_tested": run.models_tested,
        "status": run.status,
        "progress": run.progress,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if results is not None:
        payload["results"] = results
    return payload


@router.post(
    "/{project_id}/benchmarks",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("benchmark", BENCHMARK_RATE_LIMIT))],
)
async def trigger_benchmark(
    project_id: uuid.UUID, payload: BenchmarkCreate, db: DbDep, user: UserDep
) -> dict:
    """Trigger un benchmark (task Celery). 202 {run_id, status}."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    if payload.prompt_template_id is None:
        prompt = await ensure_prompt(db, project)
    else:
        prompt = await db.get(PromptTemplate, payload.prompt_template_id)
        if prompt is None or prompt.project_id != project_id:
            raise HTTPException(status_code=422, detail="prompt template not in this project")
    models = await usable_models(db, user, payload.models, project_id)
    # len(models) < len(payload.models) si un nom est inconnu OU en doublon
    # (SQL IN déduplique) → 422.
    if len(models) != len(payload.models):
        raise HTTPException(status_code=422, detail="unknown or duplicate model names")
    gold_count = (
        await db.execute(
            select(GoldArticle.id).where(GoldArticle.project_id == project_id).limit(1)
        )
    ).first()
    if gold_count is None:
        raise HTTPException(status_code=422, detail="no gold articles in this project")
    active = (
        await db.execute(
            select(BenchmarkRun.id)
            .where(
                BenchmarkRun.project_id == project_id,
                BenchmarkRun.status.in_([BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="benchmark already in progress")
    run = BenchmarkRun(
        project_id=project_id,
        prompt_template_id=prompt.id,
        models_tested=payload.models,
        status=BenchmarkStatus.PENDING,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    job = benchmark_task.delay(str(run.id))
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"run_id": str(run.id), "status": status}


@router.post("/{project_id}/benchmarks/{run_id}/cancel")
async def cancel_benchmark(
    project_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Arrête un benchmark en cours ou en attente (les résultats déjà calculés sont conservés)."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    if run.status not in (BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING):
        raise HTTPException(status_code=409, detail="benchmark not in progress")
    run.status = BenchmarkStatus.FAILED
    await db.commit()
    return _run_payload(run)


@router.get("/{project_id}/benchmarks")
async def list_benchmarks(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    runs = (
        (
            await db.execute(
                select(BenchmarkRun)
                .where(BenchmarkRun.project_id == project_id)
                .order_by(BenchmarkRun.created_at, BenchmarkRun.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_run_payload(r) for r in runs]}


@router.get("/{project_id}/benchmarks/{run_id}")
async def get_benchmark(project_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    results = (
        await db.execute(
            select(
                BenchmarkResult.model_name,
                BenchmarkResult.execution_time_seconds,
                BenchmarkResult.accuracy,
                BenchmarkResult.precision_score,
                BenchmarkResult.recall_score,
                BenchmarkResult.f1_score,
            ).where(BenchmarkResult.benchmark_run_id == run.id)
        )
    ).all()
    results_payload = [
        {
            "model_name": r.model_name,
            "execution_time_seconds": r.execution_time_seconds,
            "accuracy": r.accuracy,
            "precision_score": r.precision_score,
            "recall_score": r.recall_score,
            "f1_score": r.f1_score,
        }
        for r in results
    ]
    return _run_payload(run, results=results_payload)
