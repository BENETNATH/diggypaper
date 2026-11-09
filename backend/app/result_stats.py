"""Whole-lot statistics of a search's results (spec 2026-11-09-sous-analyse-criteres R5.2).

Deterministic compute over ``article_predictions`` — no LLM: per-field value
distribution + mean confidence, status distribution, N total. LATEST prediction
per article (house pattern). Empty fields are absent. The dict is injected
verbatim (compact JSON) into the results-mode chat context.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import ArticlePrediction, PredictionStatus, Search
from app.searches import in_search, lot_ids


async def compute_result_stats(
    db: AsyncSession, project_id: uuid.UUID, search_id: uuid.UUID
) -> dict:
    """``{"n_results": n, "status": {...}, "fields": {key: {"values": {...},
    "confidence_mean": float | None}}}`` — empty lot → empty maps."""
    search = await db.get(Search, search_id)
    if search is None or search.project_id != project_id:
        raise HTTPException(status_code=404, detail="search not found")
    lot = await lot_ids(db, search)
    if not lot:
        return {"n_results": 0, "status": {}, "fields": {}}
    p2 = aliased(ArticlePrediction)
    latest_id = (
        select(p2.id)
        .where(
            p2.article_id == ArticlePrediction.article_id,
            in_search(p2.search_id, search),
        )
        .order_by(p2.created_at.desc().nulls_last(), p2.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        (
            await db.execute(
                select(ArticlePrediction).where(
                    ArticlePrediction.article_id.in_(lot),
                    in_search(ArticlePrediction.search_id, search),
                    ArticlePrediction.id == latest_id,
                )
            )
        )
        .scalars()
        .all()
    )
    # Zero-filled: a status with no article must read 0, not be absent (the
    # chat stats block is read key by key). Absent entirely only when the lot
    # has no prediction at all (test: empty stats).
    status: dict[str, int] = {s.value: 0 for s in PredictionStatus}
    fields = search.fields or []
    values: dict[str, dict[str, int]] = {f["key"]: defaultdict(int) for f in fields}
    confs: dict[str, list[float]] = {f["key"]: [] for f in fields}
    for row in rows:
        status[str(row.status) if row.status else "NONE"] += 1
        decision = row.decision_json or {}
        fcs = row.field_confidences or {}
        for field in fields:
            key = field["key"]
            value = decision.get(key)
            if value is None:
                continue
            if value is True:
                value = "true"
            elif value is False:
                value = "false"
            else:
                value = str(value)
            values[key][value] += 1
            conf = fcs.get(key)
            if conf is None or isinstance(conf, bool) or not isinstance(conf, (int, float)):
                conf = row.confidence_score  # legacy row: global fallback
            if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                confs[key].append(float(conf))
    fields_out: dict = {}
    for field in fields:
        key = field["key"]
        if not values[key]:
            continue  # empty field → absent
        entry = {"values": dict(values[key])}
        if confs[key]:
            entry["confidence_mean"] = round(sum(confs[key]) / len(confs[key]), 4)
        fields_out[key] = entry
    return {"n_results": len(rows), "status": dict(status), "fields": fields_out}
