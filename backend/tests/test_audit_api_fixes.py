"""Audit prod (API) : export PDF borné, /health, annulation d'import, autorité de
l'invitateur au register, purge des invitations (anonymisation), rate limits, XFF."""

import io
import zipfile
from types import SimpleNamespace

import pytest
from conftest import LEGACY_FIELDS
from sqlalchemy import select

from app.db.models import (
    Article,
    ArticleFile,
    ImportJob,
    ImportJobStatus,
    Invitation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    User,
    UserRole,
)
from app.invitations import render_invitation, upsert_invitation
from app.searches import ensure_default_search
from tests.conftest import _make_user


@pytest.fixture
async def lead_project(session, researcher_user, client, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(
        title="Revue test", created_by=researcher_user.id, question="Q ?", fields=LEGACY_FIELDS
    )
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    return project


# --- export/pdfs : un PDF à la fois, 413 au-delà du ZIP 32 bits ----------------------


async def _pdf_articles(session, project, n):
    await ensure_default_search(session, project)
    arts = [Article(project_id=project.id, pmid=str(i), title=f"T{i}") for i in range(n)]
    session.add_all(arts)
    await session.flush()
    session.add_all(
        [
            ArticleFile(article_id=a.id, data=b"%PDF-1.4 " + bytes([65 + i]))
            for i, a in enumerate(arts)
        ]
    )
    await session.commit()


async def test_export_pdfs_several_members_valid_zip(client, session, lead_project):
    await _pdf_articles(session, lead_project, 4)
    r = await client.get(f"/api/v1/projects/{lead_project.id}/export/pdfs")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None
    assert sorted(z.read(n) for n in z.namelist()) == [b"%PDF-1.4 " + bytes([c]) for c in b"ABCD"]


async def test_export_pdfs_413_beyond_zip32(client, session, lead_project, monkeypatch):
    await _pdf_articles(session, lead_project, 2)
    monkeypatch.setattr("app.api.v1.export._ZIP32_MAX", 100)
    r = await client.get(f"/api/v1/projects/{lead_project.id}/export/pdfs")
    assert r.status_code == 413


async def test_export_pdfs_rate_limited(client, session, lead_project):
    await _pdf_articles(session, lead_project, 1)
    url = f"/api/v1/projects/{lead_project.id}/export/pdfs"
    codes = [(await client.get(url)).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


# --- /health ----------------------------------------------------------------------


async def test_health_ok_checks_db(client, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    r = await client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_health_503_when_redis_down(client, monkeypatch):
    async def down(url):
        raise ConnectionError("redis down")

    monkeypatch.setenv("REDIS_URL", "redis://nowhere:6379/0")
    monkeypatch.setattr("app.main._redis_ping", down)
    r = await client.get("/api/v1/health")
    assert r.status_code == 503


async def test_health_503_when_db_down(engine, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db
    from app.main import create_app

    monkeypatch.delenv("REDIS_URL", raising=False)

    class BrokenSession:
        async def execute(self, *a, **kw):
            raise OSError("db down")

    async def broken_db():
        yield BrokenSession()

    app = create_app()
    app.dependency_overrides[get_db] = broken_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://app.test") as ac:
        assert (await ac.get("/api/v1/health")).status_code == 503


# --- POST /projects/{id}/import/cancel ---------------------------------------------


@pytest.fixture
def revoked(monkeypatch):
    from app.celery_app import celery_app

    calls = []
    monkeypatch.setattr(
        celery_app.control, "revoke", lambda task_id, **kw: calls.append((task_id, kw))
    )
    return calls


@pytest.mark.parametrize("with_articles,expected", [(False, "DRAFT"), (True, "FROZEN")])
async def test_import_cancel_marks_failed_and_restores_project(
    client, session, lead_project, revoked, with_articles, expected
):
    lead_project.status = ProjectStatus.PROCESSING
    if with_articles:
        session.add(Article(project_id=lead_project.id, pmid="1", title="kept"))
    job = ImportJob(
        project_id=lead_project.id, query="q", job_id="celery-1", status=ImportJobStatus.RUNNING
    )
    session.add(job)
    await session.commit()

    r = await client.post(f"/api/v1/projects/{lead_project.id}/import/cancel")
    assert r.status_code == 200, r.text
    assert r.json() == {"cancelled": 1, "status": expected}
    await session.refresh(job)
    await session.refresh(lead_project)
    assert job.status == ImportJobStatus.FAILED and job.error == "cancelled"
    assert job.completed_at is not None
    assert lead_project.status == ProjectStatus(expected)
    assert revoked == [("celery-1", {"terminate": True})]


async def test_import_cancel_409_without_running_job(client, lead_project, revoked):
    r = await client.post(f"/api/v1/projects/{lead_project.id}/import/cancel")
    assert r.status_code == 409


async def test_import_cancel_forbidden_for_annotator(client, session, lead_project, do_login):
    ann = await _make_user(session, "ann@lab.fr", UserRole.RESEARCHER, "ann-pass-1234")
    session.add(
        ProjectMember(
            project_id=lead_project.id, user_id=ann.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    await session.commit()
    await do_login("ann@lab.fr", "ann-pass-1234", consent=True)
    assert (
        await client.post(f"/api/v1/projects/{lead_project.id}/import/cancel")
    ).status_code == 403


# --- register : autorité de l'invitateur re-vérifiée --------------------------------


async def _invite_token(session, project, inviter, email="new@lab.fr", role="LEAD"):
    inv = await upsert_invitation(
        session, email, "fr", inviter.id, project_id=project.id, role=role
    )
    _, _, link = await render_invitation(session, inv)
    await session.commit()
    return link.split("token=")[1]


async def _register(client, token):
    client.cookies.clear()
    return await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-1234"}
    )


async def test_register_ok_while_inviter_is_lead(client, session, lead_project, researcher_user):
    token = await _invite_token(session, lead_project, researcher_user)
    assert (await _register(client, token)).status_code == 201


async def test_register_401_when_inviter_demoted(client, session, lead_project, researcher_user):
    token = await _invite_token(session, lead_project, researcher_user)
    member = await session.get(ProjectMember, (lead_project.id, researcher_user.id))
    member.role_in_project = ProjectMemberRole.ANNOTATOR
    await session.commit()
    assert (await _register(client, token)).status_code == 401
    assert (
        await session.execute(select(User).where(User.email == "new@lab.fr"))
    ).scalar_one_or_none() is None


async def test_remove_member_revokes_its_pending_invitations(
    client, session, lead_project, researcher_user, do_login
):
    other = await _make_user(session, "lead2@lab.fr", UserRole.RESEARCHER, "lead2-pass-1234")
    session.add(
        ProjectMember(
            project_id=lead_project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    token = await _invite_token(session, lead_project, other)
    r = await client.delete(f"/api/v1/projects/{lead_project.id}/members/{other.id}")
    assert r.status_code == 204
    assert (await session.execute(select(Invitation))).scalars().all() == []
    assert (await _register(client, token)).status_code == 401


async def test_delete_user_revokes_issued_invitations(session, lead_project, researcher_user):
    from app.cleanup import delete_user

    await _invite_token(session, lead_project, researcher_user)
    await delete_user(session, researcher_user)
    await session.commit()
    assert (await session.execute(select(Invitation))).scalars().all() == []


# --- anonymisation : purge des invitations ------------------------------------------


async def test_anonymize_purges_invitations(client, session, lead_project, researcher_user):
    await _invite_token(session, lead_project, researcher_user, email="someone@lab.fr")
    session.add(Invitation(email="RESEARCHER@lab.fr", project_id=lead_project.id, role="LEAD"))
    keep = Invitation(email="third@lab.fr", project_id=lead_project.id, role="ANNOTATOR")
    session.add(keep)
    await session.commit()
    assert (await client.delete("/api/v1/users/me/anonymize")).status_code == 204
    session.expire_all()
    left = (await session.execute(select(Invitation.email))).scalars().all()
    assert left == ["third@lab.fr"]


# --- POST /models/external : rate limit ---------------------------------------------


async def test_register_external_model_rate_limited(client, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    codes = [
        (
            await client.post(
                "/api/v1/models/external",
                json={"name": f"ext-{i}", "base_url": "https://api.example.org/v1", "model": "m"},
            )
        ).status_code
        for i in range(11)
    ]
    assert codes[:10] == [201] * 10 and codes[10] == 429


# --- X-Forwarded-For : la dernière entrée (ajoutée par le proxy) ---------------------


def test_client_ip_is_last_xff_entry():
    from app.api.deps import get_client_ip

    req = SimpleNamespace(
        headers={"x-forwarded-for": "6.6.6.6, 10.0.0.2"}, client=SimpleNamespace(host="x")
    )
    assert get_client_ip(req) == "10.0.0.2"
    req.headers = {}
    assert get_client_ip(req) == "x"
