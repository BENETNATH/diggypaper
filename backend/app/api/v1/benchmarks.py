"""Routes benchmark (Arena) — Module 3.

Lecture : membre du projet. Trigger : LEAD du projet, rate limiting par IP +
garde « un benchmark PENDING/RUNNING par projet ».
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import _require_project_member
from app.api.v1.prompts import ensure_prompt
from app.celery_app import celery_app
from app.db.models import (
    Article,
    BenchmarkPrediction,
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
from app.schema import project_fields
from app.searches import get_search, gold_condition, lot_condition
from app.tasks.benchmark import benchmark_task

router = APIRouter(prefix="/projects", tags=["benchmarks"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

BENCHMARK_RATE_LIMIT = 5
BENCHMARK_MAX_CALLS = 3000  # articles × variantes × modèles : au-delà, réduire le lot


class Variant(BaseModel):
    """Une formulation à comparer : prompt libre, prompt enregistré, ou (rien) prompt généré."""

    name: str = Field(min_length=1, max_length=100)
    system_prompt: str | None = Field(default=None, max_length=20000)
    user_prompt_schema: str | None = Field(default=None, max_length=20000)
    prompt_template_id: uuid.UUID | None = None


class BenchmarkCreate(BaseModel):
    prompt_template_id: uuid.UUID | None = None  # variante unique (défaut : prompt généré)
    variants: list[Variant] | None = Field(default=None, max_length=10)
    models: list[str] = Field(min_length=1)
    search_id: uuid.UUID | None = None  # recherche dont le lot sert de banc d'essai
    use_gold: bool = False  # noter les variantes contre le gold (articles du lot au gold)
    gold_examples: int = Field(default=0, ge=0, le=20)  # exemples du gold ajoutés au prompt


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
        "search_id": str(run.search_id) if run.search_id else None,
        "prompt_template_id": str(run.prompt_template_id) if run.prompt_template_id else None,
        "models_tested": run.models_tested,
        "variants": run.variants,
        "options": run.options,
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
    """Lance un benchmark : chaque modèle × variante répond à chaque article du lot de la
    recherche ``search_id`` (un sous-lot : créez une recherche dédiée). 202 {run_id, status}."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    scope = await get_search(db, project, payload.search_id)
    variants = payload.variants or []
    for v in variants:
        if v.prompt_template_id is not None:
            pt = await db.get(PromptTemplate, v.prompt_template_id)
            if pt is None or pt.project_id != project_id:
                raise HTTPException(status_code=422, detail="prompt template not in this project")
    if len({v.name for v in variants}) != len(variants):
        raise HTTPException(status_code=422, detail="variant names must be unique")
    prompt_id = None
    if not variants:  # une seule formulation : le prompt choisi, sinon celui généré de la question
        if payload.prompt_template_id is None:
            prompt = await ensure_prompt(db, project, scope)
        else:
            prompt = await db.get(PromptTemplate, payload.prompt_template_id)
            if prompt is None or prompt.project_id != project_id:
                raise HTTPException(status_code=422, detail="prompt template not in this project")
        prompt_id = prompt.id
    models = await usable_models(db, user, payload.models, project_id)
    # len(models) < len(payload.models) si un nom est inconnu OU en doublon
    # (SQL IN déduplique) → 422.
    if len(models) != len(payload.models):
        raise HTTPException(status_code=422, detail="unknown or duplicate model names")
    lot = (
        await db.execute(select(func.count()).select_from(Article).where(lot_condition(scope)))
    ).scalar_one()
    if lot == 0:
        raise HTTPException(status_code=422, detail="the search has no articles")
    calls = lot * len(models) * max(1, len(variants))
    if calls > BENCHMARK_MAX_CALLS:
        raise HTTPException(
            status_code=422,
            detail=f"too many calls ({calls} > {BENCHMARK_MAX_CALLS}): use a smaller lot",
        )
    if payload.use_gold:
        gold = (
            await db.execute(
                select(func.count())
                .select_from(GoldArticle)
                .where(
                    await gold_condition(db, project, scope),
                    GoldArticle.article_id.in_(select(Article.id).where(lot_condition(scope))),
                )
            )
        ).scalar_one()
        if gold == 0:
            raise HTTPException(status_code=422, detail="no gold articles in the lot")
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
        search_id=scope.id,
        prompt_template_id=prompt_id,
        variants=[v.model_dump(mode="json", exclude_none=True) for v in variants] or None,
        options={"use_gold": payload.use_gold, "gold_examples": payload.gold_examples},
        models_tested=payload.models,
        status=BenchmarkStatus.PENDING,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    job = benchmark_task.delay(str(run.id))
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"run_id": str(run.id), "status": status, "calls": calls}


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
    rows = (
        (
            await db.execute(
                select(BenchmarkResult)
                .where(BenchmarkResult.benchmark_run_id == run.id)
                .order_by(BenchmarkResult.model_name, BenchmarkResult.variant)
            )
        )
        .scalars()
        .all()
    )
    results_payload = [
        {
            "model_name": r.model_name,
            "variant": r.variant,
            "n_scored": r.n_scored,
            "execution_time_seconds": r.execution_time_seconds,
            "accuracy": r.accuracy,
            "precision_score": r.precision_score,
            "recall_score": r.recall_score,
            "f1_score": r.f1_score,
            "details": r.details,
        }
        for r in rows
    ]
    return _run_payload(run, results=results_payload)


@router.get("/{project_id}/benchmarks/{run_id}/articles")
async def benchmark_articles(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    disagree: bool = False,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    """Grille article × variante : la réponse de chaque (modèle, variante), la référence si elle
    existe, et si les variantes divergent. ``disagree`` ne garde que les désaccords."""
    project = await _require_project_member(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    scope = await get_search(db, project, run.search_id)
    fields = project_fields(scope)
    preds = (
        (
            await db.execute(
                select(BenchmarkPrediction).where(BenchmarkPrediction.benchmark_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    by_article: dict = {}
    for p in preds:
        by_article.setdefault(p.article_id, []).append(p)
    gold: dict = {}
    if (run.options or {}).get("use_gold"):
        rows = await db.execute(select(GoldArticle).where(await gold_condition(db, project, scope)))
        gold = {g.article_id: g.ground_truth_json for g in rows.scalars()}
    titles = {
        a_id: t
        for a_id, t in (
            await db.execute(select(Article.id, Article.title).where(Article.id.in_(by_article)))
        ).all()
    }
    items = []
    for a_id, ps in by_article.items():
        outputs = [
            {
                "model": p.model_name,
                "variant": p.variant,
                "decision": p.decision_json,
                "error": p.error,
            }
            for p in sorted(ps, key=lambda p: (p.model_name, p.variant or ""))
        ]
        diff = metrics.disagrees([o["decision"] for o in outputs], fields)
        if disagree and not diff:
            continue
        items.append(
            {
                "article_id": str(a_id),
                "title": titles.get(a_id),
                "gold": gold.get(a_id),
                "disagree": diff,
                "outputs": outputs,
            }
        )
    items.sort(key=lambda i: (not i["disagree"], i["title"] or ""))
    start = max(0, (page - 1) * per_page)
    return {
        "total": len(items),
        "page": page,
        "per_page": per_page,
        "items": items[start : start + per_page],
    }
