"""Benchmark routes (Arena) - Module 3.

Read: member of the project. Trigger: LEAD of the project, rate limiting per IP +
"one PENDING/RUNNING benchmark per project" guard.
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
    BenchmarkVote,
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
from app.tasks.benchmark import benchmark_task, retry_benchmark_errors_task

router = APIRouter(prefix="/projects", tags=["benchmarks"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

BENCHMARK_RATE_LIMIT = 5
BENCHMARK_MAX_CALLS = 3000  # articles × variants × models: beyond that, reduce the batch


class Variant(BaseModel):
    """A formulation to compare: free prompt, registered prompt, or (nothing) generated prompt."""

    name: str = Field(min_length=1, max_length=100)
    system_prompt: str | None = Field(default=None, max_length=20000)
    user_prompt_schema: str | None = Field(default=None, max_length=20000)
    prompt_template_id: uuid.UUID | None = None


class BenchmarkCreate(BaseModel):
    prompt_template_id: uuid.UUID | None = None  # single variant (default: generated prompt)
    variants: list[Variant] | None = Field(default=None, max_length=10)
    models: list[str] = Field(min_length=1)
    search_id: uuid.UUID | None = None  # search whose batch is the test bench
    use_gold: bool = False  # score the variants against the gold (batch articles in the gold)
    gold_examples: int = Field(default=0, ge=0, le=20)  # gold examples added to the prompt


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _run_payload(
    run: BenchmarkRun, results: list[dict] | None = None, errors_total: int | None = None
) -> dict:
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
    if errors_total is not None:
        payload["errors_total"] = errors_total
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
    """Launches a benchmark: each model x variant answers each article of the
    search ``search_id`` batch (a sub-batch: create a dedicated search). 202 {run_id, status}."""
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
    # a single formulation: the chosen prompt, otherwise the one generated from the question
    if not variants:
        if payload.prompt_template_id is None:
            prompt = await ensure_prompt(db, project, scope)
        else:
            prompt = await db.get(PromptTemplate, payload.prompt_template_id)
            if prompt is None or prompt.project_id != project_id:
                raise HTTPException(status_code=422, detail="prompt template not in this project")
        prompt_id = prompt.id
    models = await usable_models(db, user, payload.models, project_id)
    # len(models) < len(payload.models) if a name is unknown OR duplicated
    # (SQL IN deduplicates) -> 422.
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
        created_by=user.id,
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
    """Stops a running or pending benchmark (already-computed results are kept)."""
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


@router.post("/{project_id}/benchmarks/{run_id}/rerun", status_code=202)
async def rerun_benchmark(
    project_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Relaunch a finished run (e.g. FAILED after an interrupted restart): a new PENDING run
    with the same configuration (lot, models, variants, options, prompt). The original run
    stays in the history (spec 2026-11-03 pointers)."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    if run.status in (BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING):
        raise HTTPException(status_code=409, detail="cannot rerun a benchmark in progress")
    active = (
        await db.execute(
            select(BenchmarkRun.id)
            .where(
                BenchmarkRun.project_id == project_id,
                BenchmarkRun.status.in_([BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING]),
                BenchmarkRun.id != run.id,
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="benchmark already in progress")
    new_run = BenchmarkRun(
        project_id=project_id,
        search_id=run.search_id,
        prompt_template_id=run.prompt_template_id,
        variants=run.variants,
        options=run.options,
        models_tested=run.models_tested,
        status=BenchmarkStatus.PENDING,
        created_by=user.id,
    )
    db.add(new_run)
    await db.commit()
    await db.refresh(new_run)
    job = benchmark_task.delay(str(new_run.id))
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"run_id": str(new_run.id), "status": status}


@router.post("/{project_id}/benchmarks/{run_id}/retry-errors", status_code=202)
async def retry_benchmark_errors(
    project_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Re-runs, in place, only the failed predictions of a completed run (provider
    errors, invalid JSON) — e.g. after a provider rate limit lifted. The run keeps
    its id and history; the variant results are recomputed by the task."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    if run.status != BenchmarkStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="only a completed benchmark can be retried")
    errors = (
        await db.execute(
            select(func.count())
            .select_from(BenchmarkPrediction)
            .where(
                BenchmarkPrediction.benchmark_run_id == run.id,
                BenchmarkPrediction.error.isnot(None),
            )
        )
    ).scalar_one()
    if errors == 0:
        raise HTTPException(status_code=409, detail="no failed prediction to retry")
    job = retry_benchmark_errors_task.delay(str(run.id))
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"run_id": str(run.id), "status": status, "errors": errors}


@router.delete("/{project_id}/benchmarks/{run_id}", status_code=204)
async def delete_benchmark(
    project_id: uuid.UUID, run_id: uuid.UUID, db: DbDep, user: UserDep
) -> None:
    """Delete a finished benchmark run and its results (LEAD; stop an active run first)."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    if run.status in (BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING):
        raise HTTPException(status_code=409, detail="stop the benchmark before deleting it")
    await db.delete(run)  # results / predictions: FK ondelete=CASCADE
    await db.commit()


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
    counts = dict(
        (
            await db.execute(
                select(BenchmarkPrediction.benchmark_run_id, func.count())
                .where(
                    BenchmarkPrediction.benchmark_run_id.in_([r.id for r in runs]),
                    BenchmarkPrediction.error.isnot(None),
                )
                .group_by(BenchmarkPrediction.benchmark_run_id)
            )
        ).all()
    )
    return {"items": [_run_payload(r, errors_total=counts.get(r.id, 0)) for r in runs]}


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
    errors_total = (
        await db.execute(
            select(func.count())
            .select_from(BenchmarkPrediction)
            .where(
                BenchmarkPrediction.benchmark_run_id == run.id,
                BenchmarkPrediction.error.isnot(None),
            )
        )
    ).scalar_one()
    return _run_payload(run, results=results_payload, errors_total=errors_total)


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
    """Article x variant grid: the answer of each (model, variant), the reference if it
    exists, and whether the variants diverge. ``disagree`` keeps only the disagreements."""
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
        diff = metrics.disagrees(
            [o["decision"] for o in outputs], fields, errors=[o["error"] for o in outputs]
        )
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


class VoteBody(BaseModel):
    model_name: str = Field(..., min_length=1)
    variant: str | None = None


@router.put("/{project_id}/benchmarks/{run_id}/articles/{article_id}/vote")
async def cast_vote(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    article_id: uuid.UUID,
    body: VoteBody,
    db: DbDep,
    user: UserDep,
) -> dict:
    """Set or change this member's preferred variant for an article."""
    await _require_project_member(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    existing = (
        await db.execute(
            select(BenchmarkVote).where(
                BenchmarkVote.benchmark_run_id == run_id,
                BenchmarkVote.article_id == article_id,
                BenchmarkVote.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.model_name = body.model_name
        existing.variant = body.variant
    else:
        db.add(
            BenchmarkVote(
                benchmark_run_id=run_id,
                article_id=article_id,
                user_id=user.id,
                model_name=body.model_name,
                variant=body.variant,
            )
        )
    await db.commit()
    return {"ok": True}


@router.delete("/{project_id}/benchmarks/{run_id}/articles/{article_id}/vote", status_code=204)
async def remove_vote(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
) -> None:
    """Remove this member's vote for an article."""
    await _require_project_member(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    existing = (
        await db.execute(
            select(BenchmarkVote).where(
                BenchmarkVote.benchmark_run_id == run_id,
                BenchmarkVote.article_id == article_id,
                BenchmarkVote.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()


@router.get("/{project_id}/benchmarks/{run_id}/votes")
async def get_votes(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
) -> dict:
    """Aggregated votes per variant + the current user's own votes (for the checkmarks)."""
    await _require_project_member(db, project_id, user)
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    votes = (
        (await db.execute(select(BenchmarkVote).where(BenchmarkVote.benchmark_run_id == run_id)))
        .scalars()
        .all()
    )
    # Aggregate per variant
    by_variant: dict[str, int] = {}
    my_votes: dict[str, str] = {}  # article_id -> model_name
    for v in votes:
        key = f"{v.model_name}|{v.variant or ''}"
        by_variant[key] = by_variant.get(key, 0) + 1
        if v.user_id == user.id:
            my_votes[str(v.article_id)] = v.model_name
    return {
        "by_variant": by_variant,
        "my_votes": my_votes,
        "total": len(votes),
    }
