"""Tests RGPD : anonymisation (DELETE /users/me/anonymize) + export (GET /users/me/export-data)."""

import hashlib
import re

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Article, HumanAnnotation, Project, ProjectMember, User, UserRole


async def _seed_researcher_data(session, user) -> tuple[Project, Article, HumanAnnotation]:
    project = Project(title="p")
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id)
    session.add_all([ProjectMember(project_id=project.id, user_id=user.id), article])
    await session.flush()
    annotation = HumanAnnotation(
        article_id=article.id,
        user_id=user.id,
        decision_json={"use_ai_behavior": True, "confidence_score": 0.9},
        notes="inclusion",
    )
    session.add(annotation)
    await session.commit()
    return project, article, annotation


async def test_anonymize_removes_pii_and_preserves_annotations(
    client, session, researcher_user, do_login
):
    user = researcher_user
    await _seed_researcher_data(session, user)
    original_email = user.email

    await do_login(original_email, "researcher-pass-123", consent=True)
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 204

    session.expire_all()
    u = (await session.execute(select(User).where(User.email.like("anonymized-%")))).scalar_one()
    expected_hash = hashlib.sha256(
        (get_settings().anon_salt + original_email).encode("utf-8")
    ).hexdigest()
    assert u.email.startswith("anonymized-")
    assert u.email.endswith("@invalid.local")
    # m5: 12 hex (not 8) for the anonymized email.
    assert re.fullmatch(r"anonymized-[0-9a-f]{12}@invalid\.local", u.email)
    assert u.hashed_password == ""
    assert u.full_name is None
    assert u.anonymized_hash == expected_hash

    ann = (await session.execute(select(HumanAnnotation))).scalar_one()
    assert ann.user_id is None
    assert ann.annotator_hash == expected_hash
    assert ann.decision_json == {"use_ai_behavior": True, "confidence_score": 0.9}
    assert ann.notes == "inclusion"


async def test_anonymize_makes_login_impossible(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 204
    resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert resp.status_code == 401


async def test_anonymized_user_old_token_rejected(client, researcher_user, do_login):
    """F2: after anonymization, the old access token is rejected (401 'Account anonymized')."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 204
    # The old cookie (access token) is still in the client jar.
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Account anonymized"


async def _fresh_user_and_annotation(engine):
    """Request in a fresh session (same in-memory engine) → real DB state."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh:
        u = (await fresh.execute(select(User).where(User.email.like("anonymized-%")))).scalar_one()
        ann = (await fresh.execute(select(HumanAnnotation))).scalar_one()
        return u.anonymized_hash, ann.annotator_hash, u.email


async def test_anonymize_is_idempotent(client, engine, session, researcher_user, do_login):
    """F2: double anonymization → 204, anonymized_hash and annotator_hash unchanged.

    The re-queries go through a fresh session (same in-memory engine) to read the
    real DB state, independently of the shared session's identity map.
    """
    user = researcher_user
    await _seed_researcher_data(session, user)
    original_email = user.email

    await do_login(original_email, "researcher-pass-123", consent=True)
    resp1 = await client.delete("/api/v1/users/me/anonymize")
    assert resp1.status_code == 204
    hash1, ann_hash1, email1 = await _fresh_user_and_annotation(engine)

    resp2 = await client.delete("/api/v1/users/me/anonymize")
    assert resp2.status_code == 204
    hash2, ann_hash2, email2 = await _fresh_user_and_annotation(engine)

    assert hash2 == hash1  # anonymized_hash unchanged
    assert ann_hash2 == ann_hash1  # annotator_hash unchanged
    assert email2 == email1  # anonymized email not regenerated


async def test_anonymized_admin_cannot_invite(client, admin_user, mock_mailer, do_login):
    """F2: an anonymized admin can no longer invite (401)."""
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 204
    resp = await client.post("/api/v1/auth/invite", json={"email": "x@lab.fr"})
    assert resp.status_code == 401
    assert mock_mailer.sent == []


async def test_anonymize_demotes_role_to_researcher(client, session, admin_user, do_login):
    """F2: anonymization downgrades the role to RESEARCHER (no residual admin access)."""
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 204
    session.expire_all()
    u = (await session.execute(select(User).where(User.email.like("anonymized-%")))).scalar_one()
    assert u.role == UserRole.RESEARCHER


async def test_anonymize_requires_auth(client):
    resp = await client.delete("/api/v1/users/me/anonymize")
    assert resp.status_code == 401


async def test_export_data_returns_user_memberships_annotations(
    client, session, researcher_user, do_login
):
    user = researcher_user
    project, article, annotation = await _seed_researcher_data(session, user)

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get("/api/v1/users/me/export-data")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data["user"].keys()) == {
        "id",
        "email",
        "full_name",
        "role",
        "language_preference",
        "data_consent_given",
        "created_at",
    }
    assert data["user"]["email"] == "researcher@lab.fr"
    assert data["user"]["role"] == "RESEARCHER"
    assert data["user"]["data_consent_given"] is True

    assert len(data["memberships"]) == 1
    assert data["memberships"][0]["project_id"] == str(project.id)
    assert data["memberships"][0]["role_in_project"] == "ANNOTATOR"

    assert len(data["annotations"]) == 1
    ann = data["annotations"][0]
    assert ann["id"] == str(annotation.id)
    assert ann["article_id"] == str(article.id)
    assert ann["decision_json"] == {"use_ai_behavior": True, "confidence_score": 0.9}
    assert ann["notes"] == "inclusion"

    assert "hashed_password" not in resp.text
    assert "anonymized_hash" not in resp.text


async def test_export_data_requires_auth(client):
    resp = await client.get("/api/v1/users/me/export-data")
    assert resp.status_code == 401
