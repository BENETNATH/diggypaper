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
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import embeddings
from app.celery_app import celery_app
from app.db.models import Article, SectionEmbedding, VectorizeProgress
from app.settings.service import get_setting
from app.tasks import _make_session_factory, _session_factory_override

logger = logging.getLogger(__name__)

#: Batch size for the embedding pass: the real model already chunks at 32, so
#: this matches its natural batching and gives a live progress tick per batch.
_EMBED_BATCH = 32


async def _set_progress(db: AsyncSession, project_id: uuid.UUID, **fields) -> None:
    """Upsert the project's ``vectorize_progress`` row, touching only the given
    fields (pass ``finished_at=None`` to clear it at a new run's start)."""
    row = await db.scalar(
        select(VectorizeProgress).where(VectorizeProgress.project_id == project_id)
    )
    if row is None:
        row = VectorizeProgress(project_id=project_id)
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)


def _upsert_dialect(dialect_name: str):
    """The dialect-specific INSERT for the upsert of pass 2.

    PostgreSQL and SQLite both support ``ON CONFLICT DO UPDATE`` on the
    ``uq_section_embeddings_article_idx`` unique constraint; the insert
    constructors are dialect-specific in SQLAlchemy."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return pg_insert if dialect_name == "postgresql" else sqlite_insert


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
        existing_by_idx: dict[int, list[SectionEmbedding]] = {}
        for e in (
            await db.execute(
                select(SectionEmbedding).where(SectionEmbedding.article_id == article.id)
            )
        ).scalars():
            existing_by_idx.setdefault(e.section_index, []).append(e)
        for idx, title, text in units:
            digest = live[idx]
            rows = existing_by_idx.get(idx, [])
            # Idempotent skip: exactly one row, same text, same model.
            if (
                len(rows) == 1
                and rows[0].content_hash == digest
                and rows[0].embedding_model == model_name
            ):
                continue
            # Replaced text, model change — or duplicate rows left by a
            # raced concurrent run: drop them all, the upsert below re-adds
            # exactly one (convergence).
            for row in rows:
                await db.delete(row)
            pending.append((article, idx, title, text, digest))
        for idx, rows in existing_by_idx.items():
            if idx not in live:
                for row in rows:  # section vanished (deleted / replaced text)
                    await db.delete(row)

    # Persist the run: RUNNING with total = sections to (re)embed. Committing
    # here also lands pass 1's deletes, so concurrent reads see a coherent state.
    now = datetime.now(UTC)
    await _set_progress(
        db,
        project_id,
        status="RUNNING",
        total=len(pending),
        done=0,
        model=model_name,
        started_at=now,
        finished_at=None,
    )
    await db.commit()

    # Pass 2: embed in batches (32 — the real model's natural batch size), then
    # upsert. The upsert (ON CONFLICT DO UPDATE) is the concurrency guard: if a
    # second run inserted the same (article, section_index) between our delete
    # and insert, we update that row instead of crashing on the unique
    # constraint. Each batch commits so the progress row is readable mid-run.
    vectorized = 0
    try:
        if pending:
            dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
            insert = _upsert_dialect(dialect)
            for start in range(0, len(pending), _EMBED_BATCH):
                part = pending[start : start + _EMBED_BATCH]
                vectors = emb.embed_batch([text for _, _, _, text, _ in part])
                for (article, idx, title, _, digest), vec in zip(part, vectors, strict=True):
                    stmt = insert(SectionEmbedding).values(
                        id=uuid.uuid4(),
                        article_id=article.id,
                        section_index=idx,
                        section_title=title,
                        content_hash=digest,
                        embedding=vec,
                        embedding_model=model_name,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["article_id", "section_index"],
                        set_={
                            "section_title": title,
                            "content_hash": digest,
                            "embedding": vec,
                            "embedding_model": model_name,
                        },
                    )
                    await db.execute(stmt)
                    vectorized += 1
                await _set_progress(db, project_id, done=vectorized)
                await db.commit()
        await _set_progress(db, project_id, status="DONE", done=vectorized, finished_at=now)
        await db.commit()
    except Exception:
        # Leave a coherent ERROR row so the UI never shows a stuck RUNNING.
        try:
            await db.rollback()
            await _set_progress(db, project_id, status="ERROR", done=vectorized, finished_at=now)
            await db.commit()
        finally:
            raise
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
