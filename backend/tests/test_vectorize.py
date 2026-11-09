"""RAG vectorization (spec 2026-11-07-recherche-semantique-rag, §2-3).

Embeddings are mocked (deterministic bag-of-words pseudo-vectors) — no torch in
the test suite. The DB is in-memory SQLite (the ``embedding`` column uses its
JSON variant there); the pipeline logic is exercised for real.
"""

import re

import pytest
from sqlalchemy import select, text, update

from app import embeddings as embeddings_mod
from app.db.models import Article, Project, SectionEmbedding


class FakeEmbedder:
    """Deterministic bag-of-words embedder: same tokens → close vectors."""

    def __init__(self, name="fake-bow-v1", dim=16):
        self.name = name
        self.dim = dim
        self._vocab: dict[str, int] = {}

    def _dim_for(self, token: str) -> int:
        if token not in self._vocab:
            self._vocab[token] = len(self._vocab) % self.dim
        return self._vocab[token]

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            v[self._dim_for(tok)] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture(autouse=True)
def fake_embedder():
    """Every test in this module uses the fake embedder (no model download)."""
    emb = FakeEmbedder()
    embeddings_mod._embedder = emb
    yield emb
    embeddings_mod._embedder = None


@pytest.fixture
async def rag_project(session, researcher_user):
    """A lead project with one article: 2 sections + abstract."""
    project = Project(title="RAG P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    article = Article(
        project_id=project.id,
        title="Zebrafish cryo-lesion",
        pmid="123",
        abstract="Adult zebrafish regenerate the heart after cryo-lesion.",
        sections=[
            {
                "title": "Methods",
                "kind": "methods",
                "text": "Cryoinjury was performed on zebrafish hearts.",
            },
            {
                "title": "Results",
                "kind": "results",
                "text": "Regenerating hearts showed cardiomyocyte proliferation.",
            },
        ],
    )
    session.add(article)
    await session.commit()
    return project, article


async def _embeddings_of(session, article_id):
    return list(
        (
            await session.execute(
                select(SectionEmbedding).where(SectionEmbedding.article_id == article_id)
            )
        ).scalars()
    )


async def test_model_change_reembeds_via_upsert(session, rag_project):
    """A model change must re-embed every section through the upsert path
    (ON CONFLICT DO UPDATE) — the concurrency guard — without tripping the
    unique constraint."""
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    await vectorize_project(session, project.id)
    # Simulate a model change: every stored row now references the old model.
    await session.execute(
        update(SectionEmbedding)
        .where(SectionEmbedding.article_id == article.id)
        .values(embedding_model="old-model")
    )
    await session.commit()
    result = await vectorize_project(session, project.id)
    assert result["vectorized"] == 2
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 2
    assert all(r.embedding_model == "fake-bow-v1" for r in rows)


async def test_upsert_conflict_updates_existing_row(session, rag_project):
    """The ON CONFLICT DO UPDATE branch itself: when a row already exists for
    (article, section_index), the upsert updates it instead of raising — this
    is the branch that protects a vectorize run racing another one."""
    import uuid as uuid_mod

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    project, article = rag_project
    session.add(
        SectionEmbedding(
            article_id=article.id,
            section_index=0,
            section_title="Old",
            content_hash="a" * 64,
            embedding=[0.1],
            embedding_model="fake-bow-v1",
        )
    )
    await session.commit()
    stmt = sqlite_insert(SectionEmbedding).values(
        id=uuid_mod.uuid4(),
        article_id=article.id,
        section_index=0,
        section_title="New",
        content_hash="b" * 64,
        embedding=[0.9],
        embedding_model="fake-bow-v2",
    )
    # corpus row: the conflict target is the partial unique index on
    # (article_id, section_index) WHERE search_id IS NULL
    stmt = stmt.on_conflict_do_update(
        index_elements=["article_id", "section_index"],
        index_where=text("search_id IS NULL"),
        set_={
            "section_title": "New",
            "content_hash": "b" * 64,
            "embedding": [0.9],
            "embedding_model": "fake-bow-v2",
        },
    )
    await session.execute(stmt)
    await session.commit()
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 1
    assert rows[0].section_title == "New"
    assert rows[0].embedding_model == "fake-bow-v2"


async def test_section_embedding_unique_constraint(session, rag_project):
    """The table must refuse two vectors for the same (article, section_index)
    — the DB guard that makes the vectorize task concurrency-safe."""
    from sqlalchemy.exc import IntegrityError

    project, article = rag_project
    session.add(
        SectionEmbedding(
            article_id=article.id,
            section_index=0,
            section_title="Methods",
            content_hash="a" * 64,
            embedding=[0.0],
            embedding_model="fake-bow-v1",
        )
    )
    await session.commit()
    session.add(
        SectionEmbedding(
            article_id=article.id,
            section_index=0,
            section_title="Methods",
            content_hash="b" * 64,
            embedding=[1.0],
            embedding_model="fake-bow-v1",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_vectorize_two_sections_two_vectors(session, rag_project, fake_embedder):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    stats = await vectorize_project(session, project.id)
    assert stats["vectorized"] == 2
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 2
    assert {r.section_title for r in rows} == {"Methods", "Results"}
    assert {r.section_index for r in rows} == {0, 1}
    for r in rows:
        assert r.embedding is not None and len(r.embedding) == fake_embedder.dim
        assert r.embedding_model == "fake-bow-v1"
        assert len(r.content_hash) == 64


async def test_vectorize_is_idempotent(session, rag_project):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    await vectorize_project(session, project.id)
    stats = await vectorize_project(session, project.id)
    assert stats["vectorized"] == 0
    assert len(await _embeddings_of(session, article.id)) == 2


async def test_abstract_only_article_one_vector(session, researcher_user):
    from app.tasks.vectorize import vectorize_project

    project = Project(title="RAG A", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    article = Article(
        project_id=project.id,
        title="Abstract-only",
        abstract="Only the abstract is available.",
    )
    session.add(article)
    await session.commit()
    await vectorize_project(session, project.id)
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 1
    assert rows[0].section_title == "Abstract"
    assert rows[0].section_index == 0


async def test_no_text_no_vectors(session, researcher_user):
    from app.tasks.vectorize import vectorize_project

    project = Project(title="RAG N", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    article = Article(
        project_id=project.id,
        title="Scanned, no OCR",
        fulltext_note="no_text_layer",
    )
    session.add(article)
    await session.commit()
    stats = await vectorize_project(session, project.id)
    assert stats["vectorized"] == 0
    assert len(await _embeddings_of(session, article.id)) == 0


async def test_changed_section_is_replaced(session, rag_project):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    await vectorize_project(session, project.id)
    article.sections[0]["text"] = "Cryoinjury was performed on adult zebrafish hearts, revised."
    await session.commit()
    stats = await vectorize_project(session, project.id)
    assert stats["vectorized"] == 1  # only the changed section
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 2  # one per section, the old one replaced


async def test_removed_section_vector_deleted(session, rag_project):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    await vectorize_project(session, project.id)
    article.sections = [article.sections[0]]  # drop "Results"
    await session.commit()
    await vectorize_project(session, project.id)
    rows = await _embeddings_of(session, article.id)
    assert len(rows) == 1
    assert rows[0].section_title == "Methods"


async def test_model_change_revectorizes_all(session, rag_project, fake_embedder):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    await vectorize_project(session, project.id)
    embeddings_mod._embedder = FakeEmbedder(name="fake-bow-v2")
    stats = await vectorize_project(session, project.id)
    assert stats["vectorized"] == 2  # both re-vectorized under the new model
    rows = await _embeddings_of(session, article.id)
    assert {r.embedding_model for r in rows} == {"fake-bow-v2"}


async def test_targeted_article_ids(session, rag_project, researcher_user):
    from app.tasks.vectorize import vectorize_project

    project, article = rag_project
    other = Article(project_id=project.id, title="Other", abstract="Other abstract.", pmid="999")
    session.add(other)
    await session.commit()
    stats = await vectorize_project(session, project.id, article_ids=[str(article.id)])
    assert stats["vectorized"] == 2
    assert len(await _embeddings_of(session, other.id)) == 0


async def test_vectorize_records_run_progress(session, rag_project):
    """The run persists progress so the UI can show n/N live: at the end the
    row is DONE with ``done == total`` (total = sections (re)indexed this run)."""
    from app.db.models import VectorizeProgress
    from app.tasks.vectorize import vectorize_project

    project, _article = rag_project
    await vectorize_project(session, project.id)
    p = await session.scalar(
        select(VectorizeProgress).where(VectorizeProgress.project_id == project.id)
    )
    assert p is not None
    assert p.status == "DONE"
    assert p.total == 2
    assert p.done == 2
    assert p.model == "fake-bow-v1"
    assert p.finished_at is not None


async def test_vectorize_noop_run_records_zero_total(session, rag_project):
    """A second (no-op) run records total == 0 — nothing (re)indexed — and
    still lands DONE, so the progress read is always a single coherent row."""
    from app.db.models import VectorizeProgress
    from app.tasks.vectorize import vectorize_project

    project, _article = rag_project
    await vectorize_project(session, project.id)
    result = await vectorize_project(session, project.id)
    assert result["vectorized"] == 0
    p = await session.scalar(
        select(VectorizeProgress).where(VectorizeProgress.project_id == project.id)
    )
    assert p.status == "DONE"
    assert p.total == 0
    assert p.done == 0
