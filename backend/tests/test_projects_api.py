"""Tests for the project routes (app/api/v1/projects.py) — Module 2, 100% offline."""

import uuid
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tasks_import import _ncbi_handler

from app.core.security import hash_password
from app.db.models import (
    Article,
    ImportJob,
    ImportJobStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    User,
    UserRole,
)
from app.settings.service import set_setting


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No real sleep in tests (NCBI throttle on query-volume)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


async def _make_user(session: AsyncSession, email: str, role: UserRole, password: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role,
        data_consent_given=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _create_project(client, title: str = "P1") -> str:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return resp.json()["id"]


def _mock_ncbi_for_task(monkeypatch, engine, pmids: list[str] | None = None):
    """Patches the task core (test session + mocked NCBI) for the real eager path."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks._session_factory_override", factory)
    handler = _ncbi_handler(pmids or ["111"])
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tasks.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )


async def _foreign_project(session, owner: User) -> Project:
    """Project created by another user (the current tester is not a member)."""
    project = Project(title="Foreign", created_by=owner.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=owner.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    return project


# --- POST /projects ---


async def test_create_project(client, session, researcher_user, do_login):
    """POST /projects creates the project + the creator's LEAD membership (201)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post(
        "/api/v1/projects",
        json={"title": "Revue 2026", "description": "Corpus cancer", "default_language": "fr"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Revue 2026"
    assert data["description"] == "Corpus cancer"
    assert data["default_language"] == "fr"
    assert data["status"] == "DRAFT"
    assert data["created_by"] == str(researcher_user.id)
    member = await session.get(ProjectMember, (uuid.UUID(data["id"]), researcher_user.id))
    assert member is not None
    assert member.role_in_project == ProjectMemberRole.LEAD


async def test_create_project_requires_auth(client):
    resp = await client.post("/api/v1/projects", json={"title": "P"})
    assert resp.status_code == 401


async def test_create_project_empty_title_422(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/projects", json={"title": ""})
    assert resp.status_code == 422


async def test_create_project_blank_title_422(client, researcher_user, do_login):
    """m8: blank title (spaces) -> 422 (strip + min_length after strip)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/projects", json={"title": "   "})
    assert resp.status_code == 422


# --- GET /projects (liste) ---


async def test_list_projects_member(client, session, researcher_user, do_login):
    """GET /projects returns the projects the user belongs to (200)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = await _create_project(client, title="Mon projet")
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == pid
    assert items[0]["title"] == "Mon projet"
    assert items[0]["role_in_project"] == "LEAD"
    assert items[0]["article_count"] == 0


async def test_list_projects_excludes_foreign(client, session, researcher_user, do_login):
    """Other users' projects (which the user is not a member of) are excluded."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    await _create_project(client, title="Le mien")
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    await _foreign_project(session, other)
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    titles = [p["title"] for p in resp.json()["items"]]
    assert titles == ["Le mien"]


async def test_list_projects_admin_sees_all(client, session, admin_user, researcher_user, do_login):
    """A global ADMIN sees all projects (even without membership)."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    await _foreign_project(session, other)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    titles = {p["title"] for p in resp.json()["items"]}
    assert "Foreign" in titles


async def test_list_projects_401_no_auth(client, session, researcher_user):
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 401


# --- GET /projects/{id} ---


async def test_get_project_as_member(client, session, researcher_user, do_login):
    """GET /projects/{id} by a member (200): status, article count, created_by."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    project = await session.get(Project, uuid.UUID(project_id))
    session.add(Article(project_id=project.id, pmid="1", title="A1"))
    session.add(Article(project_id=project.id, pmid="2", title="A2"))
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DRAFT"
    assert data["article_count"] == 2
    assert data["created_by"] == str(researcher_user.id)
    assert data["role_in_project"] == "LEAD"  # creator → LEAD in project_members


async def test_get_project_invited_lead_role_in_payload(client, session, researcher_user, do_login):
    """Invited LEAD (neither creator nor admin): GET /projects/{id} carries their role.

    Bug 2026-10-30: the field was missing (only ``list_projects`` added it) ->
    ``isProjectLead()`` false in the UI and the Recherches page showed
    "Only a project lead can create searches" to a real LEAD."""
    owner = await _make_user(session, "owner@lab.fr", UserRole.RESEARCHER, "owner-pass-123")
    owner.can_create_projects = True  # the local helper creates invites by default
    await session.commit()
    await do_login("owner@lab.fr", "owner-pass-123", consent=True)
    project_id = await _create_project(client)
    session.add(
        ProjectMember(
            project_id=uuid.UUID(project_id),
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["role_in_project"] == "LEAD"


async def test_get_project_non_member_403(client, session, researcher_user, do_login):
    """GET /projects/{id} by a non-member non-admin (403)."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = await _foreign_project(session, other)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{project.id}")
    assert resp.status_code == 403


async def test_get_project_admin_200(client, session, admin_user, do_login):
    """GET /projects/{id} by a global ADMIN (200, even without membership)."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = await _foreign_project(session, other)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{project.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Foreign"
    assert resp.json()["role_in_project"] is None  # admin non membre


async def test_get_project_not_found_404(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- POST /projects/{id}/import ---


def test_import_request_dates_optional_and_reversed_rejected():
    """ImportRequest: date_from/date_to optional (ISO); inverted bounds ->
    ValidationError (422 via FastAPI, no job)."""
    from datetime import date

    from pydantic import ValidationError

    from app.api.v1.projects import ImportRequest

    req = ImportRequest(query="q")
    assert req.date_from is None and req.date_to is None
    req = ImportRequest(query="q", date_from="2024-01-15", date_to="2024-06-30")
    assert (req.date_from, req.date_to) == (date(2024, 1, 15), date(2024, 6, 30))
    with pytest.raises(ValidationError):
        ImportRequest(query="q", date_from="2024-05-01", date_to="2024-01-01")


async def test_import_triggers_task(client, session, researcher_user, do_login, monkeypatch):
    """POST /projects/{id}/import triggers the Celery task (mock .delay) → 202."""
    import app.tasks as tasks

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    mock_result = MagicMock()
    mock_result.id = "job-123"
    mock_result.state = "PENDING"
    mock_delay = MagicMock(return_value=mock_result)
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", mock_delay)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/import", json={"query": "cancer AND mice"}
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == "job-123"
    assert data["status"] == "PENDING"
    mock_delay.assert_called_once_with(
        project_id, "cancer AND mice", date_from=None, date_to=None, source="pubmed"
    )


async def test_import_empty_query_422(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "   "})
    assert resp.status_code == 422


async def test_import_query_too_long_422(client, researcher_user, do_login):
    """m8: query over 2000 characters -> 422."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "a" * 2001})
    assert resp.status_code == 422


async def test_import_completes_frozen_corpus(
    client, session, researcher_user, do_login, monkeypatch
):
    """A FROZEN/COMPLETED corpus can be completed by a new import (duplicates ignored)."""
    import app.tasks as tasks

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    project = await session.get(Project, uuid.UUID(project_id))
    project.status = ProjectStatus.FROZEN
    await session.commit()
    mock_result = MagicMock()
    mock_result.id = "job-more"
    mock_result.state = "PENDING"
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", MagicMock(return_value=mock_result))
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
    assert resp.status_code == 202


async def test_import_processing_409(client, session, researcher_user, do_login):
    """Import already in progress (PROCESSING) -> 409."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    project = await session.get(Project, uuid.UUID(project_id))
    project.status = ProjectStatus.PROCESSING
    await session.commit()
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
    assert resp.status_code == 409


async def test_import_admin_allowed(client, session, admin_user, do_login, monkeypatch):
    """I4: a global ADMIN can trigger the import (even without membership)."""
    import app.tasks as tasks

    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = await _foreign_project(session, other)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    mock_result = MagicMock()
    mock_result.id = "job-admin"
    mock_result.state = "PENDING"
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", MagicMock(return_value=mock_result))
    resp = await client.post(f"/api/v1/projects/{project.id}/import", json={"query": "q"})
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "job-admin"


async def test_import_non_member_403(client, session, researcher_user, do_login):
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = await _foreign_project(session, other)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post(f"/api/v1/projects/{project.id}/import", json={"query": "q"})
    assert resp.status_code == 403


async def test_import_rate_limit_6th_429(client, researcher_user, do_login, monkeypatch):
    """M-2: 6th import same IP -> 429 rate_limited (limit 5/15min)."""
    import app.tasks as tasks

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    mock_result = MagicMock()
    mock_result.id = "job"
    mock_result.state = "PENDING"
    mock_delay = MagicMock(return_value=mock_result)
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", mock_delay)
    for i in range(5):
        resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
        assert resp.status_code == 202, f"import {i + 1} devrait passer"
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_import_active_job_409(client, session, researcher_user, do_login, monkeypatch):
    """M-2: an existing PENDING or RUNNING import_job -> 409 (one import per project)."""
    import app.tasks as tasks

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    job = ImportJob(
        project_id=uuid.UUID(project_id), query="existing", status=ImportJobStatus.PENDING
    )
    session.add(job)
    await session.commit()
    mock_delay = MagicMock()
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", mock_delay)
    # PENDING → 409
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
    assert resp.status_code == 409
    # RUNNING → 409
    job.status = ImportJobStatus.RUNNING
    await session.commit()
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "q"})
    assert resp.status_code == 409
    mock_delay.assert_not_called()


# --- GET /projects/{id}/articles ---


async def test_list_articles_paginated(client, session, researcher_user, do_login):
    """GET /projects/{id}/articles : pagination (page, per_page) + total."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    project = await session.get(Project, uuid.UUID(project_id))
    for i in range(3):
        session.add(Article(project_id=project.id, pmid=str(100 + i), title=f"Article {i}"))
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}/articles?page=1&per_page=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["per_page"] == 2
    assert len(data["items"]) == 2
    assert {a["pmid"] for a in data["items"]} <= {"100", "101", "102"}
    resp2 = await client.get(f"/api/v1/projects/{project_id}/articles?page=2&per_page=2")
    data2 = resp2.json()
    assert data2["page"] == 2
    assert len(data2["items"]) == 1
    # no duplicate across pages
    assert {a["pmid"] for a in data["items"]} & {a["pmid"] for a in data2["items"]} == set()


async def test_list_articles_default_per_page(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.get(f"/api/v1/projects/{project_id}/articles")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"items": [], "total": 0, "page": 1, "per_page": 50, "stats": {"TO_REVIEW": 0}}


async def test_list_articles_per_page_201_422(client, researcher_user, do_login):
    """I4 : per_page=201 → 422 (plafond 200)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.get(f"/api/v1/projects/{project_id}/articles?per_page=201")
    assert resp.status_code == 422


# --- POST /projects/{id}/query-volume (I1) ---


async def test_query_volume(client, researcher_user, do_login, monkeypatch):
    """I1: POST /projects/{id}/query-volume -> {count} (member, mocked NCBI)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    import app.api.v1.projects as projects_api

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/esearch.fcgi"
        assert request.url.params["retmax"] == "0"
        assert request.url.params["term"] == "cancer AND mice"
        return httpx.Response(200, json={"esearchresult": {"count": "42"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api,
        "_make_http_client",
        lambda: real_client(transport=httpx.MockTransport(handler)),
    )
    resp = await client.post(
        f"/api/v1/projects/{project_id}/query-volume", json={"query": "cancer AND mice"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 42, "max": 5000}


async def test_query_volume_non_member_403(client, session, researcher_user, do_login):
    """I1: query-volume by a non-member non-admin -> 403."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = await _foreign_project(session, other)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post(f"/api/v1/projects/{project.id}/query-volume", json={"query": "q"})
    assert resp.status_code == 403


async def test_query_volume_rate_limit_21st_429(client, researcher_user, do_login, monkeypatch):
    """M-1: 21st query-volume same IP -> 429 rate_limited (limit 20/15min)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    import app.api.v1.projects as projects_api

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"esearchresult": {"count": "1"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api,
        "_make_http_client",
        lambda: real_client(transport=httpx.MockTransport(handler)),
    )
    for i in range(20):
        resp = await client.post(f"/api/v1/projects/{project_id}/query-volume", json={"query": "q"})
        assert resp.status_code == 200, f"query-volume {i + 1} devrait passer"
    resp = await client.post(f"/api/v1/projects/{project_id}/query-volume", json={"query": "q"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_query_volume_ncbi_503_502(client, researcher_user, do_login, monkeypatch):
    """m-1: NCBI 503 -> 502 {error: ncbi_unavailable} (no 500, no key/URL)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    import app.api.v1.projects as projects_api

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api,
        "_make_http_client",
        lambda: real_client(transport=httpx.MockTransport(handler)),
    )
    resp = await client.post(f"/api/v1/projects/{project_id}/query-volume", json={"query": "q"})
    assert resp.status_code == 502
    assert resp.json() == {"error": "ncbi_unavailable"}
    assert "api_key" not in resp.text
    assert "eutils" not in resp.text


# --- GET /projects/{id}/jobs ---


async def test_list_jobs(client, session, researcher_user, do_login, engine, monkeypatch):
    """M3: GET /projects/{id}/jobs reads from import_jobs (no in-memory dict).

    m1: in eager mode, POST /import returns the real status (SUCCESS), not PENDING.
    """
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    # no job -> empty list
    resp = await client.get(f"/api/v1/projects/{project_id}/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"jobs": []}
    # a job triggered via the real eager path (no .delay mock)
    _mock_ncbi_for_task(monkeypatch, engine, ["111"])
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "cancer"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "SUCCESS"  # m1: real status in eager
    resp = await client.get(f"/api/v1/projects/{project_id}/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["task"] == "import_pubmed"
    assert jobs[0]["status"] == "COMPLETED"
    assert jobs[0]["query"] == "cancer"
    assert jobs[0]["result"] == {
        "imported": 1,
        "failed": 0,
        "total_count": 1,
        "fulltext": 1,
        "truncated": False,
    }
    assert jobs[0]["error"] is None
    assert jobs[0]["created_at"] is not None
    assert jobs[0]["completed_at"] is not None


async def test_import_failure_jobs_no_key_leak(
    client, session, researcher_user, do_login, engine, monkeypatch
):
    """C-1: failed import (NCBI 503, key defined) -> job.error without key nor URL;
    GET /jobs does not leak the NCBI key."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    await set_setting(session, "ncbi_api_key", "SECRET-NCBI-KEY-789")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks._session_factory_override", factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.tasks.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json={"query": "cancer"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "FAILURE"  # eager: real status
    resp = await client.get(f"/api/v1/projects/{project_id}/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "FAILED"
    assert jobs[0]["error"] is not None
    assert "SECRET-NCBI-KEY-789" not in jobs[0]["error"]
    assert "eutils" not in jobs[0]["error"]
    assert "api_key" not in jobs[0]["error"]
