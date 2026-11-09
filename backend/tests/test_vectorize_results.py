"""Results indexing (spec 2026-11-09-sous-analyse-criteres R4).

The existing vectorize pipeline is extended to the RESULTS scope: one
``section_embeddings`` row per (article, search) carrying the rendered answers
("Modèle rongeur : Oui (92 %) · Durée : 120 j (88 %)"). ``search_id`` non-NULL;
NULL = corpus (behavior unchanged). Idempotent on (content_hash, embedding_model);
on re-run only the changed answers are re-embedded; a search indexes only its
own lot; the corpus rows are never touched.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_vectorize import FakeEmbedder

from app import embeddings as embeddings_mod
from app.db.models import (
    Article,
    ArticlePrediction,
    PredictionStatus,
    Project,
    Search,
    SearchArticle,
    SectionEmbedding,
)

FIELDS = [
    {"key": "rodent", "label": "Modèle rongeur", "type": "boolean"},
    {"key": "duration", "label": "Durée (jours)", "type": "text"},
]


@pytest.fixture(autouse=True)
def fake_embedder():
    emb = FakeEmbedder()
    embeddings_mod._embedder = emb
    yield emb
    embeddings_mod._embedder = None


async def _results_project(session, n=3, kind="standard"):
    """Project + standard search (frozen lot) with per-article predictions."""
    project = Project(title="Res P")
    session.add(project)
    await session.commit()
    search = Search(
        project_id=project.id,
        name="A",
        question="Q ?",
        fields=FIELDS,
        kind=kind,
        lot_all=False,
    )
    session.add(search)
    await session.flush()
    decisions = [
        {"rodent": True, "duration": "120 j"},
        {"rodent": False},
        {"error": "invalid_json"},  # ERROR: nothing to index
    ]
    confs = [0.9, 0.2, None]
    fcs = [{"rodent": 0.92, "duration": 0.88}, None, None]
    statuses = [
        PredictionStatus.AUTO_ACCEPTED,
        PredictionStatus.AUTO_REJECTED,
        PredictionStatus.ERROR,
    ]
    articles = []
    for i in range(n):
        a = Article(
            project_id=project.id,
            title=f"A{i}",
            pmid=str(3000 + i),
            sections=[{"title": "Methods", "kind": "methods", "text": f"methods of {i}"}],
        )
        session.add(a)
        await session.flush()  # a.id is assigned at flush time
        articles.append(a)
        session.add(SearchArticle(search_id=search.id, article_id=a.id))
        session.add(
            ArticlePrediction(
                article_id=a.id,
                search_id=search.id,
                model_used="m",
                decision_json=decisions[i],
                confidence_score=confs[i],
                field_confidences=fcs[i],
                status=statuses[i],
            )
        )
    await session.commit()
    return project, search, articles


def _result_rows(rows):
    return [r for r in rows if r.search_id is not None]


async def test_results_index_one_row_per_renderable_article(session, fake_embedder):
    from app.tasks.vectorize import vectorize_project

    project, search, articles = await _results_project(session)
    stats = await vectorize_project(session, project.id, search_id=search.id)
    # a2 is an ERROR prediction (no answers) → not indexed.
    assert stats["vectorized"] == 2
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(SectionEmbedding.search_id == search.id)
            )
        ).scalars()
    )
    assert {r.article_id for r in rows} == {articles[0].id, articles[1].id}
    for r in rows:
        assert r.section_index == 0
        assert len(r.content_hash) == 64
        assert r.embedding is not None
    by_art = {r.article_id: r for r in rows}
    assert by_art[articles[0].id].section_title is None


async def test_results_index_idempotent(session):
    from app.tasks.vectorize import vectorize_project

    project, search, _ = await _results_project(session)
    await vectorize_project(session, project.id, search_id=search.id)
    stats = await vectorize_project(session, project.id, search_id=search.id)
    assert stats["vectorized"] == 0
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(SectionEmbedding.search_id == search.id)
            )
        ).scalars()
    )
    assert len(rows) == 2


async def test_results_rerun_only_changed_reembedded(session):
    from app.tasks.vectorize import vectorize_project

    project, search, articles = await _results_project(session)
    await vectorize_project(session, project.id, search_id=search.id)
    # Re-run: a0's answers changed, a1 unchanged.
    session.add(
        ArticlePrediction(
            article_id=articles[0].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"rodent": True, "duration": "200 j"},
            confidence_score=0.8,
            field_confidences={"rodent": 0.8, "duration": 0.7},
            status=PredictionStatus.NEEDS_HUMAN_REVIEW,
            # SQLite func.now() is second-resolution: make "later" explicit.
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    stats = await vectorize_project(session, project.id, search_id=search.id)
    assert stats["vectorized"] == 1
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(
                    SectionEmbedding.search_id == search.id,
                    SectionEmbedding.article_id == articles[0].id,
                )
            )
        ).scalars()
    )
    assert len(rows) == 1  # replaced, not duplicated


async def test_results_row_deleted_when_answers_vanish(session):
    from app.tasks.vectorize import vectorize_project

    project, search, articles = await _results_project(session)
    await vectorize_project(session, project.id, search_id=search.id)
    # Re-run where a1's new prediction carries no answer at all.
    session.add(
        ArticlePrediction(
            article_id=articles[1].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"other": "x"},
            confidence_score=0.4,
            status=PredictionStatus.NEEDS_HUMAN_REVIEW,
            # SQLite func.now() is second-resolution: make "later" explicit.
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    await vectorize_project(session, project.id, search_id=search.id)
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(
                    SectionEmbedding.search_id == search.id,
                    SectionEmbedding.article_id == articles[1].id,
                )
            )
        ).scalars()
    )
    assert rows == []


async def test_results_scoping_lot_only(session, researcher_user):
    """A search indexes only its own lot: an outside-lot article with a
    matching prediction for the same search is not indexed."""
    from app.tasks.vectorize import vectorize_project

    project, search, _ = await _results_project(session)
    outside = Article(project_id=project.id, title="OUT", pmid="7777")
    session.add(outside)
    await session.flush()
    session.add(
        ArticlePrediction(
            article_id=outside.id,
            search_id=search.id,
            model_used="m",
            decision_json={"rodent": True, "duration": "120 j"},
            confidence_score=0.9,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    await vectorize_project(session, project.id, search_id=search.id)
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(SectionEmbedding.search_id == search.id)
            )
        ).scalars()
    )
    assert outside.id not in {r.article_id for r in rows}


async def test_two_searches_disjoint_rows(session):
    """Each search keeps its own result rows (same articles, different lots)."""
    from app.tasks.vectorize import vectorize_project

    project, search_a, articles = await _results_project(session)
    search_b = Search(
        project_id=project.id,
        name="B",
        fields=FIELDS,
        kind="standard",
        lot_all=False,
    )
    session.add(search_b)
    await session.flush()
    session.add(SearchArticle(search_id=search_b.id, article_id=articles[0].id))
    session.add(
        ArticlePrediction(
            article_id=articles[0].id,
            search_id=search_b.id,
            model_used="m",
            decision_json={"rodent": True, "duration": "300 j"},
            confidence_score=0.7,
            status=PredictionStatus.AUTO_ACCEPTED,
            # Must read as NEWER than the search_a prediction (same article).
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    await vectorize_project(session, project.id, search_id=search_a.id)
    await vectorize_project(session, project.id, search_id=search_b.id)
    for s in (search_a, search_b):
        rows = list(
            (
                await session.execute(
                    select(SectionEmbedding).where(SectionEmbedding.search_id == s.id)
                )
            ).scalars()
        )
        assert rows and all(r.section_index == 0 for r in rows)
    a_rows = await session.scalar(
        select(SectionEmbedding).where(SectionEmbedding.search_id == search_a.id)
    )
    assert a_rows is not None


async def test_corpus_run_keeps_result_rows_intact(session):
    """The corpus scope must not delete or duplicate the result rows (the
    per-article existing scan is scoped by search_id)."""
    from app.tasks.vectorize import vectorize_project

    project, search, _ = await _results_project(session)
    await vectorize_project(session, project.id, search_id=search.id)
    before = len(
        list(
            (
                await session.execute(
                    select(SectionEmbedding).where(SectionEmbedding.search_id == search.id)
                )
            ).scalars()
        )
    )
    corpus = await vectorize_project(session, project.id)
    assert corpus["vectorized"] == 3  # the 3 sections (methods) of the lot articles
    after = len(
        list(
            (
                await session.execute(
                    select(SectionEmbedding).where(SectionEmbedding.search_id == search.id)
                )
            ).scalars()
        )
    )
    assert after == before  # result rows intact
    corpus_rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(SectionEmbedding.search_id.is_(None))
            )
        ).scalars()
    )
    assert len(corpus_rows) == 3


async def test_results_upsert_conflict_3col(session):
    """The ON CONFLICT branch of the RESULTS upsert: the conflict target is the
    3-column (article, section_index, search_id) unique index — a second run
    racing into the same (article, search) updates, it does not insert."""
    from uuid import uuid4

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.db.models import Search

    project = Project(title="Upsert P")
    session.add(project)
    await session.commit()
    article = Article(project_id=project.id, title="A", pmid="5000")
    session.add(article)
    await session.flush()
    s = Search(project_id=project.id, name="A", lot_all=True)
    session.add(s)
    await session.flush()
    session.add(
        SectionEmbedding(
            article_id=article.id,
            section_index=0,
            section_title=None,
            content_hash="a" * 64,
            embedding=[0.1],
            embedding_model="fake-bow-v1",
            search_id=s.id,
        )
    )
    await session.commit()
    stmt = sqlite_insert(SectionEmbedding).values(
        id=uuid4(),
        article_id=article.id,
        section_index=0,
        section_title=None,
        content_hash="b" * 64,
        embedding=[0.9],
        embedding_model="fake-bow-v2",
        search_id=s.id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["article_id", "section_index", "search_id"],
        set_={
            "section_title": None,
            "content_hash": "b" * 64,
            "embedding": [0.9],
            "embedding_model": "fake-bow-v2",
        },
    )
    await session.execute(stmt)
    await session.commit()
    rows = list(
        (
            await session.execute(
                select(SectionEmbedding).where(
                    SectionEmbedding.article_id == article.id, SectionEmbedding.search_id == s.id
                )
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].embedding_model == "fake-bow-v2"


# ---------------------------------------------------------------------------
# Rendered text — pure
# ---------------------------------------------------------------------------


def test_render_response_format():
    from app.result_text import render_response

    text = render_response(
        {"rodent": True, "duration": "120 j"},
        {"rodent": 0.92, "duration": 0.88},
        FIELDS,
    )
    assert text == "Modèle rongeur : Oui (92 %) · Durée (jours) : 120 j (88 %)"


def test_render_response_partial_and_missing_conf():
    from app.result_text import render_response

    text = render_response(
        {"rodent": False},
        None,  # legacy prediction: no per-field confidences
        FIELDS,
    )
    assert text == "Modèle rongeur : Non"


def test_render_response_no_value_none():
    from app.result_text import render_response

    assert render_response({"other": "x"}, None, FIELDS) is None
    assert render_response(None, None, FIELDS) is None
    # No fields defined (fields = null): no decision field, nothing rendered.
    assert render_response({"rodent": True}, None, None) is None
    assert render_response({"rodent": True}, None, []) is None


# ---------------------------------------------------------------------------
# Endpoint — results index state (GET/POST …/searches/{id}/results-index)
# ---------------------------------------------------------------------------


async def _member_search(session, researcher_user):
    """The fixture project + the researcher as LEAD (API access)."""
    from app.db.models import ProjectMember, ProjectMemberRole

    project, search, articles = await _results_project(session)
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    return project, search, articles


async def _blind_member_search(session, researcher_user):
    """Same fixture, but the search is blind (masked answers)."""
    from app.db.models import ProjectMember, ProjectMemberRole

    project, search, articles = await _results_project(session, kind="blind")
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    return project, search, articles


async def test_results_index_not_started(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_started"


async def test_results_index_ready(client, session, researcher_user, do_login):
    from app.tasks.vectorize import vectorize_project

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    await vectorize_project(session, project.id, search_id=search.id)
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    info = resp.json()
    assert info["status"] == "ready"
    assert info["done"] == info["total"] == 2  # a2 (ERROR) is not indexable


async def test_results_index_empty_after_run(client, session, researcher_user, do_login):
    """A lot whose predictions are all ERROR: the run completes with zero
    rows → explicit "empty" state (distinct from never-run)."""
    from app.db.models import ProjectMember, ProjectMemberRole
    from app.tasks.vectorize import vectorize_project

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="Empty P")
    session.add(project)
    await session.commit()
    search = Search(project_id=project.id, name="E", fields=FIELDS, kind="standard", lot_all=False)
    session.add(search)
    await session.flush()
    a = Article(project_id=project.id, title="A0", pmid="4000")
    session.add(a)
    await session.flush()
    session.add(SearchArticle(search_id=search.id, article_id=a.id))
    session.add(
        ArticlePrediction(
            article_id=a.id,
            search_id=search.id,
            model_used="m",
            decision_json={"error": "invalid_json"},
            status=PredictionStatus.ERROR,
        )
    )
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    await vectorize_project(session, project.id, search_id=search.id)
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.json()["status"] == "empty"


async def test_results_index_failed_state(client, session, researcher_user, do_login):
    from app.db.models import VectorizeProgress

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    session.add(VectorizeProgress(project_id=project.id, status="ERROR", done=1, total=2))
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    info = resp.json()
    assert info["status"] == "failed"
    assert (info["done"], info["total"]) == (1, 2)


async def test_results_index_retry_post_lead(
    client, session, researcher_user, do_login, monkeypatch
):
    """The retry re-triggers the results indexing pass (lead only)."""
    import app.tasks.vectorize as vectorize_mod

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    calls = []
    monkeypatch.setattr(
        vectorize_mod.vectorize_task, "delay", lambda *a, **kw: calls.append((a, kw))
    )
    resp = await client.post(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert calls == [((str(project.id),), {"search_id": str(search.id)})]


async def test_results_index_retry_requires_lead(client, session, researcher_user, do_login):
    from app.core.security import hash_password
    from app.db.models import ProjectMember, ProjectMemberRole, User, UserRole

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    annotator = User(
        email="annotator@lab.fr",
        hashed_password=hash_password("annotator-pass-123"),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
    )
    session.add(annotator)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=annotator.id,
            role_in_project=ProjectMemberRole.ANNOTATOR,
        )
    )
    await session.commit()
    await do_login("annotator@lab.fr", "annotator-pass-123", consent=True)
    resp = await client.post(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 403


async def test_results_index_get_blind_search_404(client, session, researcher_user, do_login):
    """A blind search's results index would be an oracle on the masked
    answers: 404 (no oracle) instead of the index state."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _blind_member_search(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 404


async def test_results_index_post_blind_search_404(
    client, session, researcher_user, do_login, monkeypatch
):
    """Re-indexing a blind lot is refused the same way: 404 and the task is
    never queued."""
    import app.tasks.vectorize as vectorize_mod

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _blind_member_search(session, researcher_user)
    calls = []
    monkeypatch.setattr(
        vectorize_mod.vectorize_task, "delay", lambda *a, **kw: calls.append((a, kw))
    )
    resp = await client.post(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 404
    assert calls == []


async def test_results_index_post_rate_limited(
    client, session, researcher_user, do_login, monkeypatch
):
    """POST results-index enqueues a heavy indexing pass: it carries the
    vectorize rate limit (5/15min per IP, same scope as the corpus reindex)."""
    import app.tasks.vectorize as vectorize_mod

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _member_search(session, researcher_user)
    calls = []
    monkeypatch.setattr(
        vectorize_mod.vectorize_task, "delay", lambda *a, **kw: calls.append((a, kw))
    )
    for i in range(5):
        resp = await client.post(
            f"/api/v1/projects/{project.id}/searches/{search.id}/results-index"
        )
        assert resp.status_code == 200, f"results-index {i + 1} devrait passer"
    resp = await client.post(f"/api/v1/projects/{project.id}/searches/{search.id}/results-index")
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"
    assert len(calls) == 5


async def test_result_sections_latest_predictions_single_query(session):
    """_result_sections fetches every lot article's latest prediction with ONE
    aggregate query (audit minor: no per-article N+1), with the same rendered
    content as the per-article loop it replaces."""
    from sqlalchemy import event

    from app.semantic_search import _result_sections

    project, search, articles = await _results_project(session, n=3)
    engine = session.bind.sync_engine
    queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    out = await _result_sections(session, project.id, search)

    # lot + one aggregate query — never one query per article.
    assert len(queries) <= 2, f"{len(queries)} requêtes (N+1 attendu corrigé)"
    assert sorted(o["article"].pmid for o in out) == ["3000", "3001"]
    assert all(o["index"] == 0 and o["title"] is None and o["text"] for o in out)
