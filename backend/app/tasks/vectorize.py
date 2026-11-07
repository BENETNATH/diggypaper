"""Vectorization pipeline (RAG, spec 2026-11-07-recherche-semantique-rag §3).

Incremental: a section is embedded only when its ``content_hash`` or the
embedding model changed; sections that vanished (deleted / replaced text) have
their vectors removed. The corpus is pre-chunked by section
(``Article.sections``); abstract-only articles get one synthetic "Abstract"
unit; articles with no text are skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import embeddings
from app.celery_app import celery_app
from app.db.models import Article, SectionEmbedding
from app.settings.service import get_setting
from app.tasks import _make_session_factory, _session_factory_override

logger = logging.getLogger(__name__)


def _units(article: Article) -> list[tuple[int, str | None, str]]:
    """The embeddable units of an article: its sections (original indices), or
    a synthetic ``Abstract`` unit when there is none, or none at all."""
    units: list[tuple[int, str | None, str]] = []
    for i, section in enumerate(article.sections or []):
        text = (section or {}).get("text") or ""
        if text.strip():
            units.append((i, section.get("title"), text))
    if not units:
        abstract = article.abstract or ""
        if abstract.strip():
            units = [(0, "Abstract", abstract)]
    return units


async def vectorize_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    article_ids: list[str] | None = None,
    model_name: str | None = None,
) -> dict:
    """(Re)vectorize the project's sections. Returns ``{"vectorized": n,
    "articles": m}``. Idempotent on unchanged content + model.

    ``article_ids`` (string UUIDs) targets a subset — the incremental hooks
    (import / OCR / full-text) send only the new ones.
    """
    if model_name is None:
        model_name = await get_setting(db, "embedding_model")
    emb = embeddings.get_embedder(model_name)  # None → the cached/first-load model
    model_name = emb.name  # the model that will actually produce the vectors

    q = select(Article).where(Article.project_id == project_id)
    if article_ids:
        q = q.where(Article.id.in_([uuid.UUID(a) for a in article_ids]))
    articles = list((await db.execute(q)).scalars().all())

    # Pass 1: collect the units to (re)embed.
    pending: list[
        tuple[Article, int, str | None, str, str]
    ] = []  # (article, idx, title, text, hash)
    for article in articles:
        units = _units(article)
        live: dict[int, str] = {
            idx: hashlib.sha256(text.encode("utf-8")).hexdigest() for idx, _, text in units
        }
        existing = {
            e.section_index: e
            for e in (
                await db.execute(
                    select(SectionEmbedding).where(SectionEmbedding.article_id == article.id)
                )
            ).scalars()
        }
        for idx, title, text in units:
            digest = live[idx]
            row = existing.get(idx)
            if row is not None and row.content_hash == digest and row.embedding_model == model_name:
                continue  # idempotent — same text, same model
            if row is not None:
                await db.delete(row)  # replaced text or model change
            pending.append((article, idx, title, text, digest))
        for idx, row in existing.items():
            if idx not in live:
                await db.delete(row)  # section vanished

    # Pass 2: embed in one batch (32/appel dans le vrai modèle), then upsert.
    vectorized = 0
    if pending:
        vectors = emb.embed_batch([text for _, _, _, text, _ in pending])
        for (article, idx, title, _, digest), vec in zip(pending, vectors, strict=True):
            db.add(
                SectionEmbedding(
                    article_id=article.id,
                    section_index=idx,
                    section_title=title,
                    content_hash=digest,
                    embedding=vec,
                    embedding_model=model_name,
                )
            )
            vectorized += 1
    await db.commit()
    return {"vectorized": vectorized, "articles": len(articles)}


@celery_app.task(name="diggypaper.vectorize")
def vectorize_task(project_id: str, article_ids: list[str] | None = None) -> dict:
    """Worker entry point (hooks + "Indexer le corpus" button)."""

    async def _run() -> dict:
        engine = None
        sf = _session_factory_override
        if sf is None:
            engine, sf = _make_session_factory()
        try:
            async with sf() as session:
                return await vectorize_project(session, uuid.UUID(project_id), article_ids)
        finally:
            if engine is not None:
                await engine.dispose()

    result = asyncio.run(_run())
    logger.info("vectorize project %s: %s", project_id, result)
    return result
