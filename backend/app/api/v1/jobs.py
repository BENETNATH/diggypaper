"""Global activity: number of jobs in progress on the instance
(spec 2026-11-03-activite-tracabilite §2).

Visible to any authenticated user — deliberately no project information at all:
this endpoint exists so users can see the instance is alive (other users exist).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import (
    BenchmarkRun,
    ImportJob,
    InferenceJob,
    User,
)

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["jobs"])

_ACTIVE_STATUSES = ("PENDING", "RUNNING")


async def _count(db: AsyncSession, model, statuses) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(model)
            .where(model.project_id.is_not(None), model.status.in_(statuses))
        )
    ).scalar_one()


@router.get("/jobs/active")
async def active_jobs(db: DbDep, user: UserDep) -> dict:
    """Counters of the instance's in-progress jobs (import / analysis / benchmark)."""
    import_n = await _count(db, ImportJob, _ACTIVE_STATUSES)
    analysis_n = await _count(db, InferenceJob, _ACTIVE_STATUSES)
    benchmark_n = await _count(db, BenchmarkRun, _ACTIVE_STATUSES)
    return {
        "count": import_n + analysis_n + benchmark_n,
        "by_kind": {"import": import_n, "analysis": analysis_n, "benchmark": benchmark_n},
    }
