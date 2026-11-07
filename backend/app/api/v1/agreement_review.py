"""Reviewer agreement routes (spec 2026-11-07-accord-reviews §4).

GET /projects/{project_id}/searches/{search_id}/agreement
    Auth: member (read). Optional query: user_a / user_b (default: the two
    most active reviewers), model (AI-human agreement, existing predictions
    only). Pure aggregation — no inference.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import _require_project_member
from app.db.models import User
from app.review_agreement import ai_human, build_summary, human_human, reviewer_counts
from app.searches import get_search

AGREEMENT_RATE_LIMIT = 60

router = APIRouter(
    prefix="/projects",
    tags=["agreement"],
    dependencies=[Depends(_rate_limit_dependency("agreement", AGREEMENT_RATE_LIMIT))],
)

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.get("/{project_id}/searches/{search_id}/agreement")
async def agreement(
    project_id: uuid.UUID,
    search_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    request: Request,
    user_a: uuid.UUID | None = None,
    user_b: uuid.UUID | None = None,
    model: str | None = None,
) -> dict:
    """Human-human + AI-human agreement metrics and the disagreement list."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search_id)
    counts = await reviewer_counts(db, scope)
    users = {u.id: u for u, _ in counts}

    async def _resolve(uid: uuid.UUID) -> User:
        u = users.get(uid) or await db.get(User, uid)
        if u is None:
            raise HTTPException(status_code=404, detail="reviewer not found")
        return u

    a = await _resolve(user_a) if user_a is not None else (counts[0][0] if counts else None)
    b = (
        await _resolve(user_b)
        if user_b is not None
        else (counts[1][0] if len(counts) > 1 else None)
    )
    if a is not None and b is not None and a.id == b.id:
        raise HTTPException(status_code=422, detail="user_a and user_b must differ")

    def _empty_block(user_a_ref: User | None, user_b_ref: User | None) -> dict:
        return {
            "user_a": _ref(user_a_ref),
            "user_b": _ref(user_b_ref),
            "n": 0,
            "n_agree": 0,
            "acc": None,
            "kappa": None,
            "by_field": [
                {"field": f["key"], "n": 0, "n_agree": 0, "acc": None, "kappa": None}
                for f in (scope.fields or [])
            ],
            "disagreements": [],
        }

    def _ref(u: User | None) -> dict | None:
        return {"id": str(u.id), "email": u.email} if u else None

    if a is None or b is None:
        # fewer than two reviewers with annotations: no measurable pair
        humans = _empty_block(a, b)
    else:
        humans = await human_human(db, scope, a, b)

    ai = await ai_human(db, scope, model, b) if (model and b is not None) else None
    lang = getattr(request.state, "lang", "en")
    return {
        "humans": humans,
        "ai": ai,
        "disagreements": humans["disagreements"],
        "n_reviewers": len(counts),
        "reviewers": [{"id": str(u.id), "email": u.email, "count": c} for u, c in counts],
        "summary": build_summary(lang, humans, ai, len(humans["disagreements"])),
    }
