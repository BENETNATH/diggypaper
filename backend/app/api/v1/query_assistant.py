"""PubMed query assistant (project lead): free conversation -> verified query."""

import logging
import uuid
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import query_assistant as qa
from app.api.deps import get_current_user, get_db, rate_limit_user
from app.api.v1.projects import _require_lead_or_admin, _require_project_member
from app.db.models import User
from app.europepmc import EpmcError
from app.hal import HalError
from app.inference import InferenceError, infer_with, make_inference_service
from app.model_access import usable_models
from app.openalex import OpenAlexError
from app.pubmed import NCBIError
from app.settings.service import get_setting
from app.tasks import ADAPTERS, _source_key_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["query-assistant"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=qa.MAX_USER_CHARS)


class AssistRequest(BaseModel):
    model: str = Field(min_length=1)
    source: Literal["pubmed", "europepmc", "openalex", "hal"] = "pubmed"
    messages: list[Message] = Field(min_length=1, max_length=qa.MAX_MESSAGES)
    seeds: str = Field(default="", max_length=20000)  # articles attendus : DOI / PMID, un par ligne


async def _check_source(source: str, query: str, seeds: str, db: AsyncSession) -> dict:
    """Verifies the proposed query against the engine: PubMed (volume, translation, unknown terms,
    recall); the other sources (volume only). Sanitized error -> {error: <source>_unavailable}."""
    api_key = await get_setting(db, _source_key_setting(source))
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            if source == "pubmed":
                return await qa.check_query(query, api_key, qa.parse_seeds(seeds), http)
            adapter = ADAPTERS[source](http, api_key)
            return {
                "count": await adapter.search_volume(query, None),
                "too_long": len(query) > qa.MAX_QUERY,
            }
    except (NCBIError, EpmcError, OpenAlexError, HalError) as exc:
        logger.error("query-assistant %s error: %s", source, exc)
        return {"error": "ncbi_unavailable" if source == "pubmed" else f"{source}_unavailable"}


@router.post(
    "/{project_id}/query-assistant",
    dependencies=[Depends(rate_limit_user("query_assistant", 20))],
)
async def assist(project_id: uuid.UUID, payload: AssistRequest, db: DbDep, user: UserDep) -> dict:
    """The model proposes a query for the chosen engine; the source verifies it (volume, and for
    PubMed: translation, unknown terms, recall on the expected articles)."""
    await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    if payload.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="last message must come from the user")
    models = await usable_models(db, user, [payload.model], project_id)
    if not models:
        raise HTTPException(status_code=422, detail="unknown model")
    prompt = qa.build_prompt([m.model_dump() for m in payload.messages])
    service = await make_inference_service(db)
    answer = None
    for attempt in range(2):  # un modèle qui raisonne répond parfois sans JSON : une seconde chance
        try:
            answer = await infer_with(models[0], service, qa.system_prompt(payload.source), prompt)
            break
        except InferenceError as exc:
            if str(exc) != "invalid_json" or attempt == 1:
                raise HTTPException(status_code=502, detail=f"model_error:{exc}") from exc
    if not isinstance(answer, dict):
        raise HTTPException(status_code=502, detail="model_error:invalid_json")
    logger.info(
        "query-assistant: user=%s project=%s model=%s source=%s msg=%r",
        user.id,
        project_id,
        payload.model,
        payload.source,
        payload.messages[-1].content[:80],
    )
    result = qa.constrain(answer, payload.source) | {"check": None}
    if result["query"]:
        result["check"] = await _check_source(payload.source, result["query"], payload.seeds, db)
    return result
