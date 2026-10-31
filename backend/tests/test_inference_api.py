"""Tests for the inference routes (app/api/v1/inference.py) — 100% offline."""

import uuid
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    Article,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    ModelShare,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    PromptTemplate,
    User,
    UserRole,
)


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


async def _create_frozen_project(client, session, researcher_user) -> str:
    """Creates a FROZEN project with 1 article + prompt + model (inference prerequisites)."""
    resp = await client.post("/api/v1/projects", json={"title": "P1"})
    assert resp.status_code == 201
    project_id = resp.json()["id"]
    project = await session.get(Project, uuid.UUID(project_id))
    project.status = ProjectStatus.FROZEN
    await session.commit()
    session.add(Article(project_id=project.id, pmid="111", title="A", methods_text="M."))
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Article Text:\n{methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    session.add(ModelShare(model_id=model.id, project_id=project.id))  # modèle autorisé au projet
    await session.commit()
    return project_id, str(prompt.id)


def _mock_inference_task(monkeypatch, state: str = "PENDING"):
    mock_result = MagicMock()
    mock_result.id = "inf-123"
    mock_result.state = state
    mock_delay = MagicMock(return_value=mock_result)
    import app.tasks.inference as inf_tasks

    monkeypatch.setattr(inf_tasks.inference_task, "delay", mock_delay)
    return mock_delay


# --- POST /projects/{id}/inference ---


async def test_trigger_inference_lead(client, session, researcher_user, do_login, monkeypatch):
    """Project LEAD: 202 {job_id, status} + job PERSISTED in the DB."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    mock_delay = _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["job_id"]
    mock_delay.assert_called_once_with(data["job_id"])
    job = await session.get(InferenceJob, uuid.UUID(data["job_id"]))
    assert job is not None
    assert job.status == InferenceJobStatus.PENDING
    assert job.model_used == "Kev-4B"


async def test_trigger_inference_admin(client, session, admin_user, do_login, monkeypatch):
    """Global ADMIN: allowed even without membership."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    session.add(Article(project_id=project.id, pmid="1", title="A", methods_text="M."))
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x"))
    await session.commit()
    _mock_inference_task(monkeypatch)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/inference",
        json={"prompt_template_id": str(prompt.id), "model": "Kev-4B"},
    )
    assert resp.status_code == 202


async def test_trigger_inference_non_lead_403(client, session, researcher_user, do_login):
    """Membre ANNOTATOR (pas LEAD) → 403."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    member = await session.get(ProjectMember, (uuid.UUID(project_id), researcher_user.id))
    member.role_in_project = ProjectMemberRole.ANNOTATOR
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
    )
    assert resp.status_code == 403


async def test_trigger_inference_non_member_403(client, session, researcher_user, do_login):
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/inference",
        json={"prompt_template_id": str(uuid.uuid4()), "model": "x"},
    )
    assert resp.status_code == 403


async def test_trigger_inference_not_frozen_409(client, session, researcher_user, do_login):
    """Non-FROZEN project (DRAFT) -> 409 (corpus not frozen)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/projects", json={"title": "P1"})
    project_id = resp.json()["id"]
    project = await session.get(Project, uuid.UUID(project_id))
    # reste DRAFT (pas FROZEN)
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x"))
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": str(prompt.id), "model": "Kev-4B"},
    )
    assert resp.status_code == 409


async def test_trigger_inference_unknown_prompt_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _create_frozen_project(client, session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": str(uuid.uuid4()), "model": "Kev-4B"},
    )
    assert resp.status_code == 422


async def test_trigger_inference_prompt_other_project_422(
    client, session, researcher_user, do_login
):
    """Prompt from another project -> 422 (no cross-project)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _create_frozen_project(client, session, researcher_user)
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    other_project = Project(title="Other", created_by=other.id, status=ProjectStatus.FROZEN)
    session.add(other_project)
    await session.commit()
    other_prompt = PromptTemplate(project_id=other_project.id, version_number="v1.0")
    session.add(other_prompt)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": str(other_prompt.id), "model": "Kev-4B"},
    )
    assert resp.status_code == 422


async def test_trigger_inference_unknown_model_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": prompt_id, "model": "Inexistant"},
    )
    assert resp.status_code == 422


async def test_trigger_inference_active_job_409(client, session, researcher_user, do_login):
    """An existing PENDING/RUNNING job -> 409 (one inference per project)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    for status in (InferenceJobStatus.PENDING, InferenceJobStatus.RUNNING):
        job = InferenceJob(
            project_id=uuid.UUID(project_id),
            prompt_template_id=uuid.UUID(prompt_id),
            model_used="Kev-4B",
            status=status,
        )
        session.add(job)
        await session.commit()
        resp = await client.post(
            f"/api/v1/projects/{project_id}/inference",
            json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
        )
        assert resp.status_code == 409, f"job {status} devrait bloquer"
        job.status = InferenceJobStatus.COMPLETED
        await session.commit()


async def test_trigger_inference_rate_limit_6th_429(
    client, session, researcher_user, do_login, monkeypatch
):
    """6th trigger same IP -> 429 (limit 5/15min)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    for i in range(5):
        resp = await client.post(
            f"/api/v1/projects/{project_id}/inference",
            json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
        )
        assert resp.status_code == 202, f"trigger {i + 1} devrait passer"
        jobs = (
            (
                await session.execute(
                    select(InferenceJob).where(InferenceJob.project_id == uuid.UUID(project_id))
                )
            )
            .scalars()
            .all()
        )
        for j in jobs:
            j.status = InferenceJobStatus.COMPLETED
        await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/inference",
        json={"prompt_template_id": prompt_id, "model": "Kev-4B"},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


# --- GET /projects/{id}/inference ---


async def test_get_inference_status_member(client, session, researcher_user, do_login):
    """GET /projects/{id}/inference: status of the last job + statistics."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _create_frozen_project(client, session, researcher_user)
    # aucun job → {job: null}
    resp = await client.get(f"/api/v1/projects/{project_id}/inference")
    assert resp.status_code == 200
    assert resp.json() == {"job": None}
    # a COMPLETED job with stats
    job = InferenceJob(
        project_id=uuid.UUID(project_id),
        model_used="Kev-4B",
        status=InferenceJobStatus.COMPLETED,
        result_json={
            "processed": 10,
            "auto_accepted": 5,
            "auto_rejected": 2,
            "needs_review": 3,
            "duration_seconds": 120.5,
        },
    )
    session.add(job)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}/inference")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["status"] == "COMPLETED"
    assert data["job"]["result"]["processed"] == 10
    assert data["job"]["result"]["auto_accepted"] == 5


async def test_get_inference_status_non_member_403(client, session, researcher_user, do_login):
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{project.id}/inference")
    assert resp.status_code == 403


async def test_get_inference_status_not_found_404(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/inference")
    assert resp.status_code == 404


# --- POST /projects/{id}/inference/cancel ---


async def test_cancel_inference_stops_active_job_and_allows_resume(
    client, session, researcher_user, do_login, monkeypatch
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, prompt_id = await _create_frozen_project(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    url = f"/api/v1/projects/{project_id}/inference"
    assert (await client.post(url + "/cancel")).status_code == 409  # rien en cours
    body = {"prompt_template_id": prompt_id, "model": "Kev-4B"}
    job_id = (await client.post(url, json=body)).json()["job_id"]
    r = await client.post(url + "/cancel")
    assert (
        r.status_code == 200 and r.json()["status"] == "FAILED" and r.json()["error"] == "cancelled"
    )
    job = await session.get(InferenceJob, uuid.UUID(job_id))
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED and job.completed_at is not None
    # a stopped analysis resumes, and a new one can be started
    assert (await client.post(url, json={**body, "resume": True})).status_code == 202


async def test_extra_instructions_extend_the_generated_prompt(
    client, session, researcher_user, do_login, monkeypatch
):
    """With no prompt chosen, the instruction is added to the generated prompt
    (reused if identical)."""
    from sqlalchemy import select

    from app.db.models import PromptTemplate

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _create_frozen_project(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    url = f"/api/v1/projects/{project_id}/inference"
    ids = []
    for _ in range(2):
        r = await client.post(url, json={"model": "Kev-4B", "instructions": "  Only humans.  "})
        assert r.status_code == 202, r.text
        job = await session.get(InferenceJob, uuid.UUID(r.json()["job_id"]))
        ids.append(job.prompt_template_id)
        job.status = InferenceJobStatus.COMPLETED  # une analyse à la fois
        await session.commit()
    assert ids[0] == ids[1]
    prompt = await session.get(PromptTemplate, ids[0])
    assert prompt.system_prompt.endswith("Additional instructions from the reviewer:\nOnly humans.")
    plain = await client.post(url, json={"model": "Kev-4B"})
    job = await session.get(InferenceJob, uuid.UUID(plain.json()["job_id"]))
    assert job.prompt_template_id != ids[0]  # sans consigne : le prompt généré tout court
    count = (await session.execute(select(PromptTemplate))).scalars().all()
    assert len(count) == 3  # le prompt de départ, avec consigne, sans consigne


async def test_get_inference_status_exposes_model_info_snapshot(
    client, session, researcher_user, do_login
):
    """Traceability (spec 2026-10-29-tracabilite-modele-version §4): the job returns the
    models snapshot (main + pre-filter); old job -> model_info null."""
    from datetime import UTC, datetime

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _create_frozen_project(client, session, researcher_user)
    old = InferenceJob(
        project_id=uuid.UUID(project_id),
        model_used="Kev-4B",
        status=InferenceJobStatus.FAILED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(old)
    await session.commit()
    job = (await client.get(f"/api/v1/projects/{project_id}/inference")).json()["job"]
    assert job["model_info"] is None
    info = {
        "main": {
            "name": "Kev-4B",
            "backend": "OLLAMA",
            "identifier": "kef/kev-4b",
            "version": "2" * 64,
        },
        "prefilter": {
            "name": "Small",
            "backend": "OLLAMA",
            "identifier": "qwen3:1.7b",
            "version": None,
        },
    }
    session.add(
        InferenceJob(
            project_id=uuid.UUID(project_id),
            model_used="Kev-4B",
            prefilter_model="Small",
            status=InferenceJobStatus.COMPLETED,
            model_info=info,
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    job = (await client.get(f"/api/v1/projects/{project_id}/inference")).json()["job"]
    assert job["model_info"] == info
