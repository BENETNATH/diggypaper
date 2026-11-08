"""Hybrid semantic search (RAG, spec 2026-11-07-recherche-semantique-rag §4-6).

The embedder is the deterministic bag-of-words fake from ``test_vectorize`` —
no model download. DB = in-memory SQLite (the dense top-k uses its in-Python
fallback; the ranking semantics are the same as the pgvector path).
"""

import uuid

import pytest
from test_vectorize import FakeEmbedder

from app import embeddings as embeddings_mod
from app.db.models import Article, Project, ProjectMember, ProjectMemberRole
from app.semantic_search import RRF_K, _rrf
from app.tasks.vectorize import vectorize_project


@pytest.fixture(autouse=True)
def fake_embedder():
    emb = FakeEmbedder()
    embeddings_mod._embedder = emb
    yield emb
    embeddings_mod._embedder = None


@pytest.fixture
async def search_project(session, researcher_user):
    """A lead project (2 articles, 3 sections) + a second project with one
    section that duplicates the first article's Methods text (scoping check)."""
    project = Project(title="S P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    a1 = Article(
        project_id=project.id,
        title="Zebrafish cryo",
        pmid="1",
        sections=[
            {
                "title": "Methods",
                "kind": "methods",
                "text": "Cryoinjury was performed on adult zebrafish hearts.",
            },
            {
                "title": "Results",
                "kind": "results",
                "text": "Phlda2 cells drive myocardial regeneration and coronary repair.",
            },
        ],
    )
    a2 = Article(
        project_id=project.id,
        title="Rat ventricle",
        pmid="2",
        sections=[
            {
                "title": "Methods",
                "kind": "methods",
                "text": "Rat ventricular tissue was sectioned and stained.",
            },
        ],
    )
    session.add_all([a1, a2])
    await session.commit()
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=other.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    o1 = Article(
        project_id=other.id,
        title="Other cryo",
        pmid="9",
        sections=[
            {
                "title": "Methods",
                "kind": "methods",
                "text": "Cryoinjury was performed on adult zebrafish hearts.",
            }
        ],
    )
    session.add(o1)
    await session.commit()
    return project, a1, a2, other


async def test_rrf_fuses_rankings():
    a, b, c = "a", "b", "c"
    fused = _rrf([[a, b, c], [c, a, b]])
    assert fused[a] == 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    assert fused[c] == 1 / (RRF_K + 3) + 1 / (RRF_K + 1)
    assert fused[c] > fused[b]  # second in both beats first-then-last


async def test_unindexed_corpus_empty_flag(client, session, search_project, do_login):
    project, _, _, _ = search_project
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "cryoinjury zebrafish"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"results": [], "indexed": False}


async def test_dense_closest_first(client, session, search_project, do_login):
    project, a1, _, _ = search_project
    await vectorize_project(session, project.id)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "cryoinjury zebrafish hearts"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indexed"] is True
    top = body["results"][0]
    assert top["article"]["id"] == str(a1.id)
    assert top["section_title"] == "Methods"
    assert "Cryoinjury" in top["excerpt"]
    assert top["score"] > 0


async def test_rare_term_via_api(client, session, search_project, do_login):
    project, a1, _, _ = search_project
    await vectorize_project(session, project.id)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "phlda2 coronary repair"},
    )
    assert r.status_code == 200, r.text
    top = r.json()["results"][0]
    assert top["article"]["id"] == str(a1.id)
    assert top["section_title"] == "Results"  # the only section with the term


async def test_scoping_other_project_never_returned(client, session, search_project, do_login):
    project, _, _, other = search_project
    await vectorize_project(session, project.id)
    await vectorize_project(session, other.id)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "cryoinjury zebrafish hearts"},
    )
    assert r.status_code == 200, r.text
    for item in r.json()["results"]:
        assert item["article"]["title"] != "Other cryo"  # other project is out


async def test_limit_respected(client, session, search_project, do_login):
    project, _, _, _ = search_project
    await vectorize_project(session, project.id)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "cryoinjury zebrafish hearts rat", "limit": 2},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["results"]) == 2


async def test_non_member_forbidden(client, session, search_project, do_login):
    from app.core.security import hash_password
    from app.db.models import User, UserRole

    project, _, _, _ = search_project
    u = User(
        email="outsider@lab.fr",
        hashed_password=hash_password("outsider-pass-123"),
        role=UserRole.RESEARCHER,
    )
    session.add(u)
    await session.commit()
    await do_login("outsider@lab.fr", "outsider-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/semantic-search",
        json={"query": "cryoinjury"},
    )
    assert r.status_code == 403, r.text


async def test_missing_project_404(client, session, search_project, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/semantic-search",
        json={"query": "cryoinjury"},
    )
    assert r.status_code == 404, r.text


async def test_vectorize_trigger_lead_202(client, session, search_project, do_login, monkeypatch):
    """« Indexer le corpus » (spec §5): lead enqueues the Celery task, gets 202."""
    from types import SimpleNamespace

    project, _, _, _ = search_project
    calls: list[str] = []
    monkeypatch.setattr(
        "app.tasks.vectorize.vectorize_task.delay",
        lambda project_id: calls.append(project_id) or SimpleNamespace(id="fake-task-id"),
    )
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post(f"/api/v1/projects/{project.id}/vectorize")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True and body["task_id"] == "fake-task-id"
    assert calls == [str(project.id)]  # whole project, no article subset


async def test_vectorize_trigger_outsider_403(
    client, session, search_project, do_login, monkeypatch
):
    """A non-member cannot trigger corpus indexing (lead/admin only)."""
    from types import SimpleNamespace

    from app.core.security import hash_password
    from app.db.models import User, UserRole

    project, _, _, _ = search_project
    monkeypatch.setattr(
        "app.tasks.vectorize.vectorize_task.delay",
        lambda project_id: SimpleNamespace(id="fake-task-id"),
    )
    u = User(
        email="outsider@lab.fr",
        hashed_password=hash_password("outsider-pass-123"),
        role=UserRole.RESEARCHER,
    )
    session.add(u)
    await session.commit()
    await do_login("outsider@lab.fr", "outsider-pass-123", consent=True)
    r = await client.post(f"/api/v1/projects/{project.id}/vectorize")
    assert r.status_code == 403, r.text


async def test_query_uses_stored_embedding_model(session, search_project, monkeypatch):
    """The query must be embedded with the model the project's rows were
    embedded with — even if the process cache holds a different model (a model
    switch must never mix embedding spaces in the same search)."""
    import app.semantic_search as ss
    from app import embeddings as emb_mod

    project, _, _, _ = search_project
    await vectorize_project(session, project.id)  # rows carry "fake-bow-v1"
    cached_other = FakeEmbedder(name="other-model-v9")
    emb_mod._embedder = cached_other  # the process cache holds another model
    requested: list[str | None] = []

    def spy(name=None):
        requested.append(name)
        return emb_mod._embedder

    monkeypatch.setattr(emb_mod, "get_embedder", spy)
    await ss.semantic_search(session, project.id, "cryoinjury zebrafish")
    assert requested == ["fake-bow-v1"]
