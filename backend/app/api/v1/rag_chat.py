"""Chat on the corpus (Chat RAG, spec 2026-11-07-chat-rag §3).

POST /projects/{project_id}/chat
    Auth: member (read). Body: {question, model, history?}.
    SSE events: ``token`` (chunks) → ``citations`` (the cited sections with
    their corpus refs) → ``done`` (full text, model, count). On failure an
    ``error`` event carries a sanitized InferenceError. No persistence (RGPD):
    the history is client-side only (≤ 6 pairs).
"""

import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.deps import get_current_user, get_db, rate_limit_user
from app.api.v1.projects import _require_project_member
from app.db.models import InferenceJob, InferenceJobStatus, User
from app.i18n import t
from app.inference import InferenceError, make_inference_service, stream_with
from app.model_access import usable_models
from app.rag_chat import (
    CHAT_CONTEXT_SECTIONS,
    HISTORY_MAX_PAIRS,
    HISTORY_MSG_MAX,
    build_chat_messages,
    build_context,
    extract_citations,
)
from app.semantic_search import semantic_search

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects",
    tags=["chat"],
    dependencies=[Depends(rate_limit_user("chat", 20))],
)

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


class ChatHistoryMsg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=HISTORY_MSG_MAX)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=200)
    history: list[ChatHistoryMsg] = Field(default_factory=list, max_length=2 * HISTORY_MAX_PAIRS)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{project_id}/chat")
async def chat(
    project_id: uuid.UUID, payload: ChatRequest, request: Request, db: DbDep, user: UserDep
) -> StreamingResponse:
    """Streams a grounded answer over the project corpus (SSE)."""
    await _require_project_member(db, project_id, user)
    lang = getattr(request.state, "lang", "en")
    models = await usable_models(db, user, [payload.model], project_id)
    if not models:
        raise HTTPException(
            status_code=400, detail=f"unknown_model — {t(lang, 'chat_unknown_model')}"
        )
    model = models[0]
    found = await semantic_search(db, project_id, payload.question, limit=CHAT_CONTEXT_SECTIONS)
    if not found["results"]:
        raise HTTPException(
            status_code=400, detail=f"corpus_empty — {t(lang, 'chat_corpus_empty')}"
        )
    busy = (
        await db.execute(
            select(InferenceJob.id)
            .where(
                InferenceJob.project_id == project_id,
                InferenceJob.status.in_([InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING]),
                InferenceJob.model_used == payload.model,
            )
            .limit(1)
        )
    ).first()
    if busy is not None:
        raise HTTPException(status_code=409, detail=f"model_busy — {t(lang, 'chat_model_busy')}")

    context_block, included = build_context(found["results"])
    messages = build_chat_messages(
        t(lang, "chat_system_prompt"),
        [m.model_dump() for m in payload.history],
        payload.question,
        context_block,
    )
    service = await make_inference_service(db)
    citations = [
        {
            "n": s["n"],
            "article_id": s["article"]["id"],
            "title": s["article"]["title"],
            "excerpt": s["excerpt"],
        }
        for s in included
    ]

    async def generate():
        parts: list[str] = []
        try:
            async for chunk in stream_with(model, service, messages):
                parts.append(chunk)
                yield _sse("token", {"t": chunk})
        except InferenceError as exc:
            logger.info("rag-chat error project=%s model=%s: %s", project_id, payload.model, exc)
            yield _sse("error", {"detail": str(exc)})
            return
        text = "".join(parts)
        cited = extract_citations(text, len(included))
        if cited:
            yield _sse("citations", [c for c in citations if c["n"] in cited])
        yield _sse("done", {"full_text": text, "model": payload.model, "n_citations": len(cited)})
        logger.info(
            "rag-chat: user=%s project=%s model=%s q=%r citations=%d",
            user.id,
            project_id,
            payload.model,
            payload.question[:80],
            len(cited),
        )

    return StreamingResponse(generate(), media_type="text/event-stream")
