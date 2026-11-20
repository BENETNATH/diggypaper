"""spec 2026-11-20-corpus-view R1: the whole corpus, listed without any analysis.

A project without any search (R9: nothing is auto-created) must still expose its
articles: ``GET /projects/{id}/articles`` without ``search`` → all the project's
articles, prediction fields null, ``stats = null``. Search-only parameters are
refused with a 400 (« no analysis in this project »)."""

import uuid

import pytest

from app.db.models import Article, Project, ProjectMember, ProjectMemberRole


@pytest.fixture
async def corpusless(client, session, researcher_user, do_login):
    """LEAD project with articles and NO search at all."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="Corpus", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    arts = [Article(project_id=project.id, pmid=str(i), title=f"Article {i}") for i in range(1, 4)]
    session.add_all(arts)
    await session.commit()
    return project, arts


def base(project):
    return f"/api/v1/projects/{project.id}/articles"


async def test_corpus_list_without_search_returns_all_articles(client, corpusless):
    project, arts = corpusless
    r = await client.get(base(project))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == len(arts)
    assert sorted(i["title"] for i in data["items"]) == [f"Article {i}" for i in range(1, 4)]
    assert data["stats"] is None
    for item in data["items"]:
        assert item["prediction_status"] is None
        assert item["confidence"] is None
        assert item["field_confidences"] is None
        assert item["model_json"] is None
        assert item["human"] is None
        assert item["review"] is None
        assert item["answers"] == []
        assert item["annotated"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"status": "AUTO_ACCEPTED"},
        {"answers": "{}"},
        {"run": str(uuid.uuid4())},
        {"sort": "score_desc"},
    ],
)
async def test_search_only_params_without_search_are_400(client, corpusless, params):
    project, _ = corpusless
    r = await client.get(base(project), params=params)
    assert r.status_code == 400
    assert r.json()["detail"] == "no analysis in this project"


async def test_corpus_list_keeps_q_fulltext_sort_and_pagination(client, corpusless):
    project, arts = corpusless
    # q filters on the title
    r = await client.get(base(project), params={"q": "Article 2"})
    assert r.status_code == 200
    assert [i["title"] for i in r.json()["items"]] == ["Article 2"]
    # fulltext: no article has one → empty with fulltext=true, all with false
    assert (await client.get(base(project), params={"fulltext": "true"})).json()["total"] == 0
    assert (await client.get(base(project), params={"fulltext": "false"})).json()["total"] == 3
    # sort date_desc / date_asc are accepted
    for sort in ("date_desc", "date_asc", "added"):
        r = await client.get(base(project), params={"sort": sort})
        assert r.status_code == 200
    # pagination
    r = await client.get(base(project), params={"page": 2, "per_page": 2})
    data = r.json()
    assert data["total"] == 3 and data["page"] == 2 and len(data["items"]) == 1


async def test_explicit_search_unchanged(
    client, session, researcher_user, do_login, default_search
):
    """With a search, the scoped code path is untouched (stats computed, 404 for foreign)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="S", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    s = await default_search(project)
    session.add_all([Article(project_id=project.id, pmid="1", title="A1")])
    await session.commit()
    r = await client.get(base(project), params={"search": str(s.id)})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["stats"], dict) and data["total"] == 1

    # a search from another project is still a 404 (resolve_search invariant)
    other = Project(title="O", created_by=None)
    session.add(other)
    await session.commit()
    foreign = await default_search(other)
    await session.commit()
    assert (await client.get(base(project), params={"search": str(foreign.id)})).status_code == 404
