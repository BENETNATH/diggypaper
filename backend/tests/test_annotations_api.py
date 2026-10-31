"""Tests for the annotation routes (app/api/v1/annotations.py) — MODULE 5, 100% offline.

Annotation viewer endpoints:
- GET  /projects/{project_id}/articles/{article_id}            (article alone + methods_text)
- GET  /projects/{project_id}/articles/{article_id}/prediction (the article's prediction)
- POST /projects/{project_id}/articles/{article_id}/annotation (annotation submission)
"""

import uuid
from datetime import UTC

from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
)

VALID_ANNOTATION = {
    "use_ai_behavior": True,
    "animal_sex": "BOTH_SEXES",
    "confidence_score": 0.9,
    "evidence_quotes": ["mice were randomized"],
    "reasoning_summary": "ok",
}


async def _make_project_with_article(session, user) -> tuple[Project, Article]:
    project = Project(title="P", created_by=user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    article = Article(
        project_id=project.id,
        pmid="111",
        title="A study",
        methods_text="Mice were randomized to groups.",
    )
    session.add(article)
    await session.commit()
    return project, article


async def _make_prediction(session, article) -> ArticlePrediction:
    p = ArticlePrediction(
        article_id=article.id,
        model_used="qwen3.8",
        decision_json={
            "use_ai_behavior": True,
            "animal_sex": "BOTH_SEXES",
            "confidence_score": 0.9,
            "evidence_quotes": ["mice were randomized"],
            "reasoning_summary": "ok",
        },
        confidence_score=0.9,
        evidence_quotes=["mice were randomized"],
    )
    session.add(p)
    await session.commit()
    return p


# --- GET /projects/{id}/articles/{article_id} ---


async def test_get_article_returns_methods_text(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(article.id)
    assert body["pmid"] == "111"
    assert body["title"] == "A study"
    assert body["methods_text"] == "Mice were randomized to groups."


async def test_get_article_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_article_404_foreign_project(client, session, researcher_user, do_login):
    """Article from another project → 404 (no cross-project leak)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="222", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{foreign.id}")
    assert resp.status_code == 404


async def test_get_article_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    # Projet dont le researcher n'est pas membre.
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="333", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{other.id}/articles/{foreign.id}")
    assert resp.status_code == 403


async def test_get_article_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}")
    assert resp.status_code == 401


# --- GET /projects/{id}/articles/{article_id}/prediction ---


async def test_get_prediction_returns_prediction(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    p = await _make_prediction(session, article)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(p.id)
    assert body["model_used"] == "qwen3.8"
    assert body["confidence_score"] == 0.9
    assert body["evidence_quotes"] == ["mice were randomized"]
    assert body["status"] == "NEEDS_HUMAN_REVIEW"


async def test_get_prediction_exposes_model_identifier_and_version(
    client, session, researcher_user, do_login
):
    """Traceability (spec 2026-10-29-tracabilite-modele-version §4): the model's
    identifier and version
    frozen at prediction time; old prediction → keys present, set to null."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    p = await _make_prediction(session, article)
    url = f"/api/v1/projects/{project.id}/articles/{article.id}/prediction"
    body = (await client.get(url)).json()
    assert body["model_identifier"] is None and body["model_version"] is None
    p.model_identifier, p.model_version = "qwen3:8b", "2" * 64
    await session.commit()
    body = (await client.get(url)).json()
    assert body["model_used"] == "qwen3.8"
    assert (body["model_identifier"], body["model_version"]) == ("qwen3:8b", "2" * 64)


async def test_get_prediction_returns_latest(client, session, researcher_user, do_login):
    """Several predictions → the most recent one (created_at) is returned."""
    from datetime import datetime

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    old = ArticlePrediction(
        article_id=article.id,
        model_used="old-model",
        confidence_score=0.5,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    new = ArticlePrediction(
        article_id=article.id,
        model_used="new-model",
        confidence_score=0.9,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add_all([old, new])
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "new-model"


async def test_get_prediction_nulls_last(client, session, researcher_user, do_login):
    """Old prediction (created_at NULL) + recent → the recent one is returned.

    Guarantees consistent SQLite/PostgreSQL behavior (NULLS LAST in DESC).
    """
    from datetime import UTC, datetime

    from sqlalchemy import text

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    # Old prediction (created_at NULL, like pre-migration rows).
    # The server_default (CURRENT_TIMESTAMP) would fill the column via the ORM;
    # it is reset to NULL by a SQL UPDATE after commit.
    old = ArticlePrediction(article_id=article.id, model_used="legacy-model")
    new = ArticlePrediction(
        article_id=article.id,
        model_used="new-model",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add_all([old, new])
    await session.commit()
    # The format stored in SQLite is dashless (32 hex) → .hex is used.
    await session.execute(
        text("UPDATE article_predictions SET created_at = NULL WHERE id = :pid"),
        {"pid": old.id.hex},
    )
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "new-model"


async def test_get_prediction_null_when_absent(client, session, researcher_user, do_login):
    """No prediction → 200 with null (the viewer pre-fills empty)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_get_prediction_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}/prediction")
    assert resp.status_code == 404


async def test_get_prediction_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="444", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{other.id}/articles/{foreign.id}/prediction")
    assert resp.status_code == 403


async def test_get_prediction_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 401


# --- POST /projects/{id}/articles/{article_id}/annotation ---


async def test_create_annotation(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["article_id"] == str(article.id)
    assert body["user_id"] == str(researcher_user.id)
    assert body["decision_json"]["use_ai_behavior"] is True
    assert body["decision_json"]["animal_sex"] == "BOTH_SEXES"
    assert body["decision_json"]["confidence_score"] == 0.9
    assert body["decision_json"]["evidence_quotes"] == ["mice were randomized"]
    # Persistance en base.
    ann = await session.get(HumanAnnotation, uuid.UUID(body["id"]))
    assert ann is not None
    assert ann.user_id == researcher_user.id


async def test_create_annotation_stores_reasoning_in_notes(
    client, session, researcher_user, do_login
):
    """reasoning_summary is kept in ``notes`` (dedicated Text column)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 201
    ann = await session.get(HumanAnnotation, uuid.UUID(resp.json()["id"]))
    assert ann.notes == "ok"


async def test_create_annotation_422_confidence_out_of_range(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "confidence_score": 1.5}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_confidence_negative(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "confidence_score": -0.1}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_invalid_animal_sex(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "animal_sex": "ANDROGYNE"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_missing_key(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {k: v for k, v in VALID_ANNOTATION.items() if k != "use_ai_behavior"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 404


async def test_create_annotation_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="555", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{other.id}/articles/{foreign.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 403


async def test_create_annotation_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 401
