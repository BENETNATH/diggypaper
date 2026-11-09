"""Filter by criteria over a search's results (spec 2026-11-09-sous-analyse-criteres R1).

AND over 3 families — response (field + op + value), confidence range
[0..1, bounds inclusive], status (multi) — evaluated on the LATEST prediction
per article of the source search's lot (house pattern of ``article_filter``).
Empty criteria = the whole lot. An unknown field yields 0 (not an error).
Blind search: criteria unavailable (oracle on the masked answers).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, ArticlePrediction, Search
from app.searches import in_search, lot_condition

_OPS = ("=", "!=", "contains")
_MAX_STATUSES = 4
_VALID_STATUSES = ("AUTO_ACCEPTED", "AUTO_REJECTED", "NEEDS_HUMAN_REVIEW", "ERROR")


def _valid_bound(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and 0 <= v <= 1)


def _validate(criteria: dict) -> None:
    if not isinstance(criteria, dict):
        raise HTTPException(status_code=422, detail="criteria must be an object")
    statuses = criteria.get("status")
    if statuses is not None:
        if (
            not isinstance(statuses, list)
            or not statuses
            or len(statuses) > _MAX_STATUSES
            or any(s not in _VALID_STATUSES for s in statuses)
        ):
            raise HTTPException(status_code=422, detail="invalid status criteria")
    conf = criteria.get("confidence")
    if conf is not None:
        if not isinstance(conf, list) or len(conf) != 2 or any(not _valid_bound(c) for c in conf):
            raise HTTPException(status_code=422, detail="invalid confidence criteria")
    response = criteria.get("response")
    if response is not None:
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("field"), str)
            or not response.get("field")
            or response.get("op") not in _OPS
            or not isinstance(response.get("value"), str)
            or not response.get("value")
        ):
            raise HTTPException(status_code=422, detail="invalid response criteria")


def _conditions(search: Search, criteria: dict) -> list:
    """Article conditions (the lot + one per family set)."""

    def latest(col):
        return (
            select(col)
            .where(
                ArticlePrediction.article_id == Article.id,
                in_search(ArticlePrediction.search_id, search),
            )
            .order_by(ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc())
            .limit(1)
            .scalar_subquery()
        )

    conds = [lot_condition(search)]
    statuses = criteria.get("status")
    if statuses:
        conds.append(latest(ArticlePrediction.status).in_(statuses))
    conf = criteria.get("confidence")
    if conf:
        pconf = latest(ArticlePrediction.confidence_score)
        lo, hi = conf
        # A missing confidence never matches a bounded range (SQL NULL).
        if lo is not None:
            conds.append(pconf >= lo)
        if hi is not None:
            conds.append(pconf <= hi)
    response = criteria.get("response")
    if response:
        key = response.get("field")
        if not key or not any(f.get("key") == key for f in (search.fields or [])):
            # Unknown field: 0 results (the frontend lists the fields anyway).
            conds.append(Article.id.is_(None))
        else:
            pdec = latest(ArticlePrediction.decision_json)
            col = cast(pdec.op("->>")(key), String)
            value = response["value"]
            if response["op"] == "contains":
                esc = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                conds.append(col.ilike(f"%{esc}%", escape="\\"))
            elif response["op"] == "!=":
                # SQLite 1/0 vs PostgreSQL 'true'/'false' for booleans.
                if value.lower() in ("true", "1"):
                    conds.append(col.notin_(["true", "1"]))
                elif value.lower() in ("false", "0"):
                    conds.append(col.notin_(["false", "0"]))
                else:
                    conds.append(col != value)
            else:  # "="
                if value.lower() in ("true", "1"):
                    conds.append(col.in_(["true", "1"]))
                elif value.lower() in ("false", "0"):
                    conds.append(col.in_(["false", "0"]))
                else:
                    conds.append(col == value)
    return conds


async def _search_for(db: AsyncSession, project_id: uuid.UUID, search_id: uuid.UUID) -> Search:
    search = await db.get(Search, search_id)
    if search is None or search.project_id != project_id:
        raise HTTPException(status_code=404, detail="search not found")
    if search.kind == "blind":
        # Blind sample: a criteria would be an oracle on the masked answers.
        raise HTTPException(status_code=422, detail="criteria unavailable on a blind search")
    return search


async def article_ids_by_criteria(
    db: AsyncSession, project_id: uuid.UUID, search_id: uuid.UUID, criteria: dict
) -> list[uuid.UUID]:
    """The lot's articles answering to ALL the criteria (stable order)."""
    await _search_for(db, project_id, search_id)
    _validate(criteria)
    search = await db.get(Search, search_id)
    conds = _conditions(search, criteria)
    return list((await db.execute(select(Article.id).where(*conds).order_by(Article.id))).scalars())


async def count_by_criteria(
    db: AsyncSession, project_id: uuid.UUID, search_id: uuid.UUID, criteria: dict
) -> int:
    """How many lot articles answer to the criteria (the live counter, R1)."""
    await _search_for(db, project_id, search_id)
    _validate(criteria)
    search = await db.get(Search, search_id)
    conds = _conditions(search, criteria)
    return (await db.execute(select(func.count()).select_from(Article).where(*conds))).scalar_one()
