"""Tests for the article deletion routes (LEAD/ADMIN only, cascading).

- DELETE /api/v1/projects/{project_id}/articles/{article_id}   : one article
- POST   /api/v1/projects/{project_id}/articles/bulk-delete    : a series of articles
"""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import (
    Article,
    ArticleFile,
    ArticlePrediction,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)


async def _make_lead_project(session, user, title="P") -> Project:
    project = Project(title=title, created_by=user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    return project


async def _make_article(session, project, title="A study") -> Article:
    art = Article(project_id=project.id, pmid=f"pm-{title}", title=title, methods_text="methods")
    session.add(art)
    await session.commit()
    return art


async def _add_children(session, article):
    f = ArticleFile(article_id=article.id, data=b"pdf-bytes")
    p = ArticlePrediction(
        article_id=article.id,
        model_used="qwen3.8",
        decision_json={"design": "RCT", "confidence_score": 0.9},
        confidence_score=0.9,
    )
    a = HumanAnnotation(article_id=article.id, decision_json={"design": "RCT"})
    session.add_all([f, p, a])
    await session.commit()
    return f, p, a


async def _exists(session, article_id) -> bool:
    row = await session.execute(select(Article).where(Article.id == article_id))
    return row.scalar_one_or_none() is not None


async def _make_annotator(session, project, email, password) -> None:
    """Adds a second user as ANNOTATOR (not lead) of the project; caller logs them in."""
    ann = User(
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
    )
    session.add(ann)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=ann.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    await session.commit()


RESEARCHER = ("researcher@lab.fr", "researcher-pass-123")


# --- DELETE one article ---


@pytest.mark.asyncio
async def test_delete_article_lead_success(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    art = await _make_article(session, project)

    resp = await client.delete(f"/api/v1/projects/{project.id}/articles/{art.id}")
    assert resp.status_code == 204, resp.text
    assert not await _exists(session, art.id)


@pytest.mark.asyncio
async def test_delete_article_cascades(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    art = await _make_article(session, project)
    f, p, a = await _add_children(session, art)

    resp = await client.delete(f"/api/v1/projects/{project.id}/articles/{art.id}")
    assert resp.status_code == 204, resp.text
    for model, key, val in (
        (ArticleFile, ArticleFile.article_id, f.article_id),
        (ArticlePrediction, ArticlePrediction.article_id, p.article_id),
        (HumanAnnotation, HumanAnnotation.article_id, a.article_id),
    ):
        got = (await session.execute(select(model).where(key == val))).scalar_one_or_none()
        assert got is None, model.__tablename__


@pytest.mark.asyncio
async def test_delete_article_forbidden_non_lead(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    art = await _make_article(session, project)
    await _make_annotator(session, project, "annotator@lab.fr", "annotator-pass-123")
    await do_login("annotator@lab.fr", "annotator-pass-123", consent=True)

    resp = await client.delete(f"/api/v1/projects/{project.id}/articles/{art.id}")
    assert resp.status_code == 403
    assert await _exists(session, art.id)  # untouched


@pytest.mark.asyncio
async def test_delete_article_404_foreign_project(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    other = await _make_lead_project(session, researcher_user, title="Other")
    foreign = await _make_article(session, other, title="Foreign")

    resp = await client.delete(f"/api/v1/projects/{project.id}/articles/{foreign.id}")
    assert resp.status_code == 404
    assert await _exists(session, foreign.id)  # untouched


@pytest.mark.asyncio
async def test_delete_article_401_unauth(client, session, researcher_user):
    project = await _make_lead_project(session, researcher_user)
    art = await _make_article(session, project)
    resp = await client.delete(f"/api/v1/projects/{project.id}/articles/{art.id}")
    assert resp.status_code == 401


# --- POST bulk-delete ---


@pytest.mark.asyncio
async def test_bulk_delete_success(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    a1 = await _make_article(session, project, title="one")
    a2 = await _make_article(session, project, title="two")

    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/bulk-delete",
        json={"article_ids": [str(a1.id), str(a2.id)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 2}
    ids = (
        (await session.execute(select(Article.id).where(Article.project_id == project.id)))
        .scalars()
        .all()
    )
    assert ids == []


@pytest.mark.asyncio
async def test_bulk_delete_ignores_foreign_project(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    other = await _make_lead_project(session, researcher_user, title="Other")
    mine = await _make_article(session, project, title="mine")
    foreign = await _make_article(session, other, title="foreign")

    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/bulk-delete",
        json={"article_ids": [str(mine.id), str(foreign.id)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1}
    assert await _exists(session, foreign.id)  # foreign project article untouched


@pytest.mark.asyncio
async def test_bulk_delete_empty(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/bulk-delete",
        json={"article_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 0}


@pytest.mark.asyncio
async def test_bulk_delete_forbidden_non_lead(client, session, researcher_user, do_login):
    await do_login(*RESEARCHER, consent=True)
    project = await _make_lead_project(session, researcher_user)
    art = await _make_article(session, project)
    await _make_annotator(session, project, "annotator2@lab.fr", "annotator2-pass-123")
    await do_login("annotator2@lab.fr", "annotator2-pass-123", consent=True)

    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/bulk-delete",
        json={"article_ids": [str(art.id)]},
    )
    assert resp.status_code == 403
    assert await _exists(session, art.id)  # untouched
