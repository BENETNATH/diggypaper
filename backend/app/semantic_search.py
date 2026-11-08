"""Hybrid semantic search core (RAG, spec 2026-11-07-recherche-semantique-rag §4).

Dense (pgvector cosine — the HNSW index on PostgreSQL, in-Python fallback on
other dialects for the test suite) + lexical (portable BM25-lite scorer over
the section texts) fused with Reciprocal Rank Fusion (k=60). Results only ever
reference sections that exist in the project.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import embeddings
from app.db.models import Article, SectionEmbedding

#: Reciprocal rank fusion constant (spec §4).
RRF_K = 60
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

#: Dense candidates per requested result (hybrid recall before the fusion).
_CANDIDATE_FACTOR = 3
_EXCERPT_LEN = 400
_EXCERPT_BACK = 120


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(x * x for x in b) ** 0.5 or 1.0
    return dot / (na * nb)


def _as_list(vec: object) -> list[float] | None:
    """Embedding as a Python float list (JSON on SQLite, numpy on pgvector)."""
    if vec is None:
        return None
    return [float(x) for x in vec]  # type: ignore[union-attr]


async def _project_sections(db: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """All embeddable sections of the project:
    ``{"article", "index", "title", "text"}`` (synthetic Abstract included)."""
    articles = list(
        (await db.execute(select(Article).where(Article.project_id == project_id))).scalars().all()
    )
    out: list[dict] = []
    for article in articles:
        sections = article.sections or []
        for i, section in enumerate(sections):
            text = (section or {}).get("text") or ""
            if text.strip():
                out.append(
                    {"article": article, "index": i, "title": section.get("title"), "text": text}
                )
        if not sections and (article.abstract or "").strip():
            out.append(
                {"article": article, "index": 0, "title": "Abstract", "text": article.abstract}
            )
    return out


async def _dense_topk(
    db: AsyncSession,
    project_id: uuid.UUID,
    q_vec: list[float],
    k: int,
    article_ids: set[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, int, float]]:
    """Top-k sections by cosine similarity: (article_id, section_index, score).

    ``article_ids`` optionally restricts the candidates to those articles
    (scoping the ranking, not the output).

    PostgreSQL: the pgvector cosine operator (``<=>``, served by the HNSW
    index). Other dialects (SQLite tests): an in-Python scan of the project's
    vectors — same ranking semantics."""
    bind = db.bind
    if bind is not None and bind.dialect.name == "postgresql":
        where = [Article.project_id == project_id]
        if article_ids is not None:
            where.append(Article.id.in_(list(article_ids)))
        distance = SectionEmbedding.embedding.cosine_distance(q_vec)
        rows = (
            await db.execute(
                select(SectionEmbedding, distance)
                .join(Article, Article.id == SectionEmbedding.article_id)
                .where(*where)
                .order_by(distance)
                .limit(k)
            )
        ).all()
        # Defensive: a legacy row duplicated by a pre-constraint race would
        # otherwise be returned twice (RRF would double-count it). Keep the
        # nearest vector of each (article, section).
        seen: set[tuple[uuid.UUID, int]] = set()
        out: list[tuple[uuid.UUID, int, float]] = []
        for se, dist in rows:
            key = (se.article_id, se.section_index)
            if key in seen:
                continue
            seen.add(key)
            out.append((se.article_id, se.section_index, 1.0 - float(dist)))
        return out
    sections = await _project_sections(db, project_id)
    vectors = {
        (se.article_id, se.section_index): _as_list(se.embedding)
        for se in (
            await db.execute(
                select(SectionEmbedding)
                .join(Article, Article.id == SectionEmbedding.article_id)
                .where(Article.project_id == project_id)
            )
        ).scalars()
    }
    scored: list[tuple[tuple[uuid.UUID, int], float]] = []
    for s in sections:
        if article_ids is not None and s["article"].id not in article_ids:
            continue
        vec = vectors.get((s["article"].id, s["index"]))
        if vec is None:
            continue
        scored.append(((s["article"].id, s["index"]), _cosine(q_vec, vec)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(key[0], key[1], score) for (key, score) in scored[:k]]


def _lexical_rank(sections: list[dict], query_tokens: list[str]) -> list[tuple[uuid.UUID, int]]:
    """BM25-lite: per-token idf over the project's sections, tf-weighted.
    A rare term in one section dominates — that is the lexical half's job."""
    docs: list[tuple[uuid.UUID, int, Counter[str]]] = []
    df: Counter[str] = Counter()
    for s in sections:
        tf = Counter(_tokens(s["text"]))
        docs.append((s["article"].id, s["index"], tf))
        df.update(tf.keys())
    n = len(docs)
    scored: list[tuple[tuple[uuid.UUID, int], float]] = []
    for article_id, index, tf in docs:
        key = (article_id, index)
        score = 0.0
        length = max(1, sum(tf.values()))
        for term in set(query_tokens):
            if tf.get(term):
                idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                score += idf * (tf[term] / length)
        if score > 0.0:
            scored.append((key, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [key for key, _ in scored]


def _rrf(rankings: list[list[tuple[uuid.UUID, int]]]) -> dict[tuple[uuid.UUID, int], float]:
    """Reciprocal rank fusion (k=60, spec §4) over several ranked lists."""
    out: dict[tuple[uuid.UUID, int], float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            out[key] = out.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return out


def _excerpt(text: str, query_tokens: list[str]) -> str:
    """~400 chars around the first query term when present, else the start."""
    low = text.lower()
    pos = -1
    for term in query_tokens:
        p = low.find(term)
        if p != -1:
            pos = p
            break
    if pos == -1:
        return text[:_EXCERPT_LEN]
    start = max(0, pos - _EXCERPT_BACK)
    return text[start : start + _EXCERPT_LEN]


async def _stored_embedding_model(db: AsyncSession, project_id: uuid.UUID) -> str | None:
    """The model the project's vectors were produced with (None when the corpus
    is empty). The query must be embedded in that same space — a model change
    must never mix vectors from different spaces in one search. During a
    model-change re-vectorization the table is transiently mixed; the majority
    model wins until the rerun completes."""
    row = (
        await db.execute(
            select(SectionEmbedding.embedding_model, func.count())
            .join(Article, Article.id == SectionEmbedding.article_id)
            .where(Article.project_id == project_id)
            .group_by(SectionEmbedding.embedding_model)
            .order_by(func.count().desc())
        )
    ).first()
    return row[0] if row else None


async def semantic_search(
    db: AsyncSession,
    project_id: uuid.UUID,
    query: str,
    limit: int = DEFAULT_LIMIT,
    article_ids: set[uuid.UUID] | None = None,
) -> dict:
    """Hybrid search over the project's sections. Returns ``{"results": [...],
    "indexed": bool}`` — an unvectorized corpus yields an empty result list
    with ``indexed: false`` (the client shows the "not indexed yet" state).

    ``article_ids``, when given, restricts the RANKING to those articles —
    not merely the final list, so a small scope keeps its own top-N instead
    of losing every candidate to the whole-corpus ranking (chat scoped to a
    frozen lot)."""
    sections = await _project_sections(db, project_id)
    if article_ids is not None:
        sections = [s for s in sections if s["article"].id in article_ids]
    indexed = bool(
        (
            await db.execute(
                select(SectionEmbedding.id)
                .join(Article, Article.id == SectionEmbedding.article_id)
                .where(Article.project_id == project_id)
                .limit(1)
            )
        ).first()
        is not None
    )
    # Spec §4: an unvectorized corpus yields an empty result with the flag —
    # the client shows the "not indexed yet" state (no lexical-only fallback).
    if not indexed:
        return {"results": [], "indexed": False}
    query_tokens = _tokens(query)
    if not sections or not query_tokens:
        return {"results": [], "indexed": indexed}

    q_vec = embeddings.get_embedder(await _stored_embedding_model(db, project_id)).embed(query)
    k = min(limit * _CANDIDATE_FACTOR, MAX_LIMIT)
    dense = await _dense_topk(db, project_id, q_vec, k, article_ids)
    lexical = _lexical_rank(sections, query_tokens)
    fused = _rrf([[(a, i) for a, i, _ in dense], lexical])

    by_key = {(s["article"].id, s["index"]): s for s in sections}
    results: list[dict] = []
    for (article_id, index), score in sorted(fused.items(), key=lambda x: x[1], reverse=True):
        if len(results) >= limit:
            break
        s = by_key.get((article_id, index))
        if s is None:  # defensive: only existing sections
            continue
        article = s["article"]
        results.append(
            {
                "article": {
                    "id": str(article.id),
                    "title": article.title,
                    "pmid": article.pmid,
                    "doi": article.doi,
                },
                "section_title": s["title"],
                "excerpt": _excerpt(s["text"], query_tokens),
                "score": round(score, 6),
            }
        )
    return {"results": results, "indexed": indexed}
