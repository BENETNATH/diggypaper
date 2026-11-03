"""Activity & traceability tests — spec 2026-11-03-activite-tracabilite.

§1 GET /projects/{id}/jobs/active (members) — running imports / analyses / benchmarks.
§2 GET /jobs/active (any authenticated user) — global counters, no project detail.
§3 created_by on jobs + GET /projects/{id}/activity (members) + GET /admin/activity (admin).
"""

import uuid
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    BenchmarkRun,
    BenchmarkStatus,
    ImportJob,
    ImportJobStatus,
    InferenceJob,
    InferenceJobStatus,
    Project,
    ProjectStatus,
    Search,
    User,
    UserRole,
)


async def _make_user(
    session: AsyncSession,
    email: str,
    role: UserRole,
    full_name: str | None = None,
    password: str = "pass-1234567890",
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role,
        data_consent_given=True,
        can_create_projects=True,
        full_name=full_name,
    )
    session.add(user)
    await session.commit()
    return user


async def _member_project(client, session, user) -> str:
    """A FROZEN project owned by `user`; its auto-created default search
    is renamed « Question A » for the assertions."""
    resp = await client.post("/api/v1/projects", json={"title": "P1"})
    assert resp.status_code == 201
    project_id = resp.json()["id"]
    project = await session.get(Project, uuid.UUID(project_id))
    project.status = ProjectStatus.FROZEN
    search = (
        (await session.execute(select(Search).where(Search.project_id == project.id)))
        .scalars()
        .first()
    )
    assert search is not None  # created automatically with the project
    search.name = "Question A"
    await session.commit()
    return project_id


async def _seed_jobs(session, project_id: str, owner: User):
    """One RUNNING job per family + one COMPLETED (must never be listed as active)."""
    project = await session.get(Project, uuid.UUID(project_id))
    search = (
        (await session.execute(select(Search).where(Search.project_id == project.id)))
        .scalars()
        .first()
    )
    import_job = ImportJob(
        project_id=project.id,
        query="rat AND diabetes",
        status=ImportJobStatus.RUNNING,
        created_by=owner.id,
    )
    analysis = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        model_used="qwen3:8b",
        status=InferenceJobStatus.RUNNING,
        created_by=owner.id,
    )
    benchmark = BenchmarkRun(
        project_id=project.id,
        search_id=search.id,
        status=BenchmarkStatus.RUNNING,
        models_tested=["qwen3:8b", "gemma3:4b"],
        progress={"done": 3, "total": 10},
        created_by=owner.id,
    )
    finished = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        model_used="qwen3:8b",
        status=InferenceJobStatus.COMPLETED,
        created_by=owner.id,
    )
    session.add_all([import_job, analysis, benchmark, finished])
    await session.commit()
    return {
        k: v.id
        for k, v in {"import": import_job, "analysis": analysis, "benchmark": benchmark}.items()
    }


# ---------------------------------------------------------------- §1 jobs/active


async def test_project_active_jobs_member(client, session, researcher_user, do_login):
    """A member sees the project's running jobs (import, analysis, benchmark) only."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _member_project(client, session, researcher_user)
    ids = await _seed_jobs(session, project_id, researcher_user)

    resp = await client.get(f"/api/v1/projects/{project_id}/jobs/active")
    assert resp.status_code == 200
    data = resp.json()
    kinds = {j["kind"] for j in data["jobs"]}
    assert kinds == {"import", "analysis", "benchmark"}
    by_kind = {j["kind"]: j for j in data["jobs"]}
    assert by_kind["benchmark"]["id"] == str(ids["benchmark"])
    assert by_kind["benchmark"]["progress"] == {"done": 3, "total": 10}
    assert by_kind["benchmark"]["models"] == ["qwen3:8b", "gemma3:4b"]
    assert by_kind["analysis"]["detail"] == "Question A"
    assert by_kind["import"]["detail"] == "rat AND diabetes"
    assert all(j["status"] in ("PENDING", "RUNNING") for j in data["jobs"])


async def test_project_active_jobs_non_member_403(client, session, researcher_user, do_login):
    """A non-member gets 403 (no job information leaks)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _member_project(client, session, researcher_user)
    await _seed_jobs(session, project_id, researcher_user)

    await _make_user(session, "other@lab.fr", UserRole.RESEARCHER)
    await do_login("other@lab.fr", "pass-1234567890", consent=True)
    resp = await client.get(f"/api/v1/projects/{project_id}/jobs/active")
    assert resp.status_code == 403


# ---------------------------------------------------------------- §2 /jobs/active


async def test_global_active_jobs_any_user_counts_only(client, session, researcher_user, do_login):
    """Any authenticated user gets the global counters — no project information.

    The researcher is a member of NOTHING: the jobs belong to other projects."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    lead = await _make_user(session, "lead@lab.fr", UserRole.RESEARCHER)

    resp = await client.post(
        "/api/v1/auth/login", json={"email": "lead@lab.fr", "password": "pass-1234567890"}
    )
    assert resp.status_code == 200
    project_id = await _member_project(client, session, lead)
    await _seed_jobs(session, project_id, lead)
    # back to the researcher (a non-member of that project)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)

    resp = await client.get("/api/v1/jobs/active")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["by_kind"] == {"import": 1, "analysis": 1, "benchmark": 1}
    # no project information at all
    assert set(data.keys()) == {"count", "by_kind"}


# ---------------------------------------------------------------- §3 created_by


async def test_benchmark_creation_sets_created_by(
    client, session, researcher_user, do_login, monkeypatch
):
    """POST /projects/{id}/benchmarks stores the launcher in created_by."""
    from tests.test_benchmarks_api import (
        _create_project,
        _mock_benchmark_task,
        _seed_benchmark_prereqs,
    )

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    _mock_benchmark_task(monkeypatch)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name]},
    )
    assert resp.status_code == 202, resp.text
    run = (
        (
            await session.execute(
                select(BenchmarkRun).where(BenchmarkRun.project_id == uuid.UUID(project_id))
            )
        )
        .scalars()
        .first()
    )
    assert run is not None
    assert run.created_by == researcher_user.id


async def test_inference_creation_sets_created_by(
    client, session, researcher_user, do_login, monkeypatch
):
    """POST /projects/{id}/inference stores the launcher in created_by."""

    from tests.test_inference_api import _create_frozen_project, _mock_inference_task

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    _mock_inference_task(monkeypatch, state="PENDING")

    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
    )
    assert resp.status_code == 202, resp.text
    job = (
        (
            await session.execute(
                select(InferenceJob).where(InferenceJob.project_id == uuid.UUID(project_id))
            )
        )
        .scalars()
        .first()
    )
    assert job.created_by == researcher_user.id


async def test_import_task_stores_created_by(session, engine):
    """import_pubmed_task(user_id=…) persists the launcher on the import job."""
    from unittest.mock import patch

    import httpx

    from app.db.models import Project, ProjectStatus
    from app.settings.service import set_setting
    from app.tasks import _import_pubmed
    from tests.test_tasks_import import _ncbi_handler  # reuse the NCBI mock

    # a project + the contact-email setting the import needs
    eng = engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        owner = await _make_user(s, "importer@lab.fr", UserRole.RESEARCHER)
        project = Project(title="P1", status=ProjectStatus.DRAFT, created_by=owner.id)
        s.add(project)
        await s.commit()
        project_id = str(project.id)
        await set_setting(s, "contact_email", "importer@lab.fr")

    monkey_http = httpx.MockTransport(_ncbi_handler(["123"]))
    with patch("app.pubmed._sleep", new=AsyncMock()):
        async with httpx.AsyncClient(transport=monkey_http) as http:
            result = await _import_pubmed(
                project_id, "query", http=http, session_factory=factory, user_id=str(owner.id)
            )
    assert result["imported"] >= 1
    async with factory() as s:
        jobs = (
            (await s.execute(select(ImportJob).where(ImportJob.project_id == project.id)))
            .scalars()
            .all()
        )
    assert jobs and jobs[0].created_by == owner.id


# ---------------------------------------------------------------- §3 activity


async def test_project_activity_member(client, session, researcher_user, do_login):
    """GET /projects/{id}/activity: the recent actions, newest first, with the user."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _member_project(client, session, researcher_user)
    await _seed_jobs(session, project_id, researcher_user)
    # the launcher has a full_name → it is displayed (not the email)
    researcher_user.full_name = "Benoît D"
    await session.commit()

    resp = await client.get(f"/api/v1/projects/{project_id}/activity")
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    # 3 active jobs + 1 completed = 4 actions
    assert len(actions) == 4
    assert all(a["user"] == "Benoît D" for a in actions)
    assert {a["type"] for a in actions} == {"import", "analysis", "benchmark"}
    analysis = next(a for a in actions if a["type"] == "analysis")
    assert analysis["detail"] == "Question A"
    assert analysis["status"] in ("RUNNING", "COMPLETED")
    started = [a["started_at"] for a in actions]
    assert started == sorted(started, reverse=True)


async def test_project_activity_unknown_user_null(client, session, researcher_user, do_login):
    """A job created before the feature (created_by NULL) shows user = null."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _member_project(client, session, researcher_user)
    project = await session.get(Project, uuid.UUID(project_id))
    search = (
        (await session.execute(select(Search).where(Search.project_id == project.id)))
        .scalars()
        .first()
    )
    session.add(
        InferenceJob(
            project_id=project.id,
            search_id=search.id,
            model_used="old-model",
            status=InferenceJobStatus.COMPLETED,
        )
    )
    await session.commit()

    resp = await client.get(f"/api/v1/projects/{project_id}/activity")
    actions = resp.json()["actions"]
    assert any(a["user"] is None and a["type"] == "analysis" for a in actions)


async def test_project_activity_non_member_403(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _member_project(client, session, researcher_user)
    await _seed_jobs(session, project_id, researcher_user)

    await _make_user(session, "other2@lab.fr", UserRole.RESEARCHER)
    await do_login("other2@lab.fr", "pass-1234567890", consent=True)
    resp = await client.get(f"/api/v1/projects/{project_id}/activity")
    assert resp.status_code == 403


async def test_admin_activity_global(client, session, admin_user, do_login):
    """GET /admin/activity (admin): the actions of ALL projects, with project_title."""
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    lead = await _make_user(session, "lead2@lab.fr", UserRole.RESEARCHER, full_name="Lead Two")

    await client.post(
        "/api/v1/auth/login", json={"email": "lead2@lab.fr", "password": "pass-1234567890"}
    )
    project_id = await _member_project(client, session, lead)
    await _seed_jobs(session, project_id, lead)
    # back to the admin (the session cookie was lead2's)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)

    resp = await client.get("/api/v1/admin/activity")
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 4
    assert all(a["project_title"] == "P1" for a in actions)
    assert all(a["user"] == "Lead Two" for a in actions)
    assert {a["type"] for a in actions} == {"import", "analysis", "benchmark"}


async def test_admin_activity_researcher_403(client, session, researcher_user, do_login):
    """A non-admin researcher gets 403 on the global activity log."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get("/api/v1/admin/activity")
    assert resp.status_code == 403
