"""Benchmark route tests (app/api/v1/benchmarks.py) — 100% offline."""

import uuid
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    Article,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    ModelShare,
    Project,
    ProjectMember,
    ProjectMemberRole,
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


async def _create_project(client, title: str = "P1") -> str:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _seed_benchmark_prereqs(session, project_id: str) -> tuple[str, str]:
    """Creates prompt + model + 1 gold article; returns (prompt_id, model_name)."""
    project = await session.get(Project, uuid.UUID(project_id))
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
    article = Article(project_id=project.id, pmid="111", title="A", methods_text="Methods.")
    session.add(article)
    await session.commit()
    session.add(
        GoldArticle(
            project_id=project.id,
            article_id=article.id,
            ground_truth_json={
                "use_ai_behavior": True,
                "animal_sex": "BOTH_SEXES",
                "confidence_score": 0.9,
                "evidence_quotes": ["mice were randomized"],
                "reasoning_summary": "ok",
            },
        )
    )
    await session.commit()
    return str(prompt.id), model.name


def _mock_benchmark_task(monkeypatch, state: str = "PENDING"):
    mock_result = MagicMock()
    mock_result.id = "bench-123"
    mock_result.state = state
    mock_delay = MagicMock(return_value=mock_result)
    import app.tasks.benchmark as bench_tasks

    monkeypatch.setattr(bench_tasks.benchmark_task, "delay", mock_delay)
    return mock_delay


# --- POST /projects/{id}/benchmarks ---


async def test_trigger_benchmark_lead(client, session, researcher_user, do_login, monkeypatch):
    """Project LEAD: 202 {run_id, status} + run PERSISTED in the DB."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    mock_delay = _mock_benchmark_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name]},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["run_id"]
    mock_delay.assert_called_once_with(data["run_id"])
    run = await session.get(BenchmarkRun, uuid.UUID(data["run_id"]))
    assert run is not None
    assert run.status == BenchmarkStatus.PENDING
    assert run.models_tested == [model_name]


async def test_trigger_benchmark_admin(client, session, admin_user, do_login, monkeypatch):
    """Global ADMIN: allowed even without membership."""
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    prompt_id, model_name = await _seed_benchmark_prereqs(session, str(project.id))
    _mock_benchmark_task(monkeypatch)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name]},
    )
    assert resp.status_code == 202


async def test_trigger_benchmark_non_lead_403(client, session, researcher_user, do_login):
    """ANNOTATOR member (not LEAD) → 403."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    # the creator is LEAD; demote them to ANNOTATOR
    member = await session.get(ProjectMember, (uuid.UUID(project_id), researcher_user.id))
    member.role_in_project = ProjectMemberRole.ANNOTATOR
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name]},
    )
    assert resp.status_code == 403


async def test_trigger_benchmark_non_member_403(client, session, researcher_user, do_login):
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id)
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
        f"/api/v1/projects/{project.id}/benchmarks",
        json={"prompt_template_id": "00000000-0000-0000-0000-000000000000", "models": ["x"]},
    )
    assert resp.status_code == 403


async def test_trigger_benchmark_unknown_prompt_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    await _seed_benchmark_prereqs(session, project_id)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={
            "prompt_template_id": str(uuid.uuid4()),
            "models": ["Kev-4B"],
        },
    )
    assert resp.status_code == 422


async def test_trigger_benchmark_prompt_other_project_422(
    client, session, researcher_user, do_login
):
    """Prompt from another project → 422 (no cross-project)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    await _seed_benchmark_prereqs(session, project_id)
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    other_project = Project(title="Other", created_by=other.id)
    session.add(other_project)
    await session.commit()
    other_prompt = PromptTemplate(project_id=other_project.id, version_number="v1.0")
    session.add(other_prompt)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": str(other_prompt.id), "models": ["Kev-4B"]},
    )
    assert resp.status_code == 422


async def test_trigger_benchmark_unknown_model_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, _ = await _seed_benchmark_prereqs(session, project_id)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": ["Inexistant"]},
    )
    assert resp.status_code == 422


async def test_trigger_benchmark_duplicate_models_422(client, session, researcher_user, do_login):
    """Duplicate in models → 422 (len(models) != len(set))."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name, model_name]},
    )
    assert resp.status_code == 422


async def test_trigger_benchmark_no_gold_422(client, session, researcher_user, do_login):
    """Aucun gold article → 422."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    project = await session.get(Project, uuid.UUID(project_id))
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    session.add(ModelShare(model_id=model.id, project_id=project.id))
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": str(prompt.id), "models": ["Kev-4B"]},
    )
    assert resp.status_code == 422


async def test_trigger_benchmark_active_run_409(client, session, researcher_user, do_login):
    """An existing PENDING/RUNNING run → 409 (one benchmark per project)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    for status in (BenchmarkStatus.PENDING, BenchmarkStatus.RUNNING):
        run = BenchmarkRun(
            project_id=uuid.UUID(project_id),
            prompt_template_id=uuid.UUID(prompt_id),
            models_tested=[model_name],
            status=status,
        )
        session.add(run)
        await session.commit()
        resp = await client.post(
            f"/api/v1/projects/{project_id}/benchmarks",
            json={"prompt_template_id": prompt_id, "models": [model_name]},
        )
        assert resp.status_code == 409, f"run {status} devrait bloquer"
        run.status = BenchmarkStatus.COMPLETED
        await session.commit()


async def test_trigger_benchmark_rate_limit_6th_429(
    client, session, researcher_user, do_login, monkeypatch
):
    """6th trigger same IP → 429 (limit 5/15min)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    _mock_benchmark_task(monkeypatch)
    for i in range(5):
        resp = await client.post(
            f"/api/v1/projects/{project_id}/benchmarks",
            json={"prompt_template_id": prompt_id, "models": [model_name]},
        )
        assert resp.status_code == 202, f"trigger {i + 1} devrait passer"
        # move the run to COMPLETED so the 409 guard does not block
        runs = (
            (
                await session.execute(
                    select(BenchmarkRun).where(BenchmarkRun.project_id == uuid.UUID(project_id))
                )
            )
            .scalars()
            .all()
        )
        for r in runs:
            r.status = BenchmarkStatus.COMPLETED
        await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/benchmarks",
        json={"prompt_template_id": prompt_id, "models": [model_name]},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


# --- GET /projects/{id}/benchmarks ---


async def test_list_benchmarks_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.get(f"/api/v1/projects/{project_id}/benchmarks")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_list_benchmarks_non_member_403(client, session, researcher_user, do_login):
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    project = Project(title="Foreign", created_by=other.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=other.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{project.id}/benchmarks")
    assert resp.status_code == 403


async def test_list_benchmarks_not_found_404(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/benchmarks")
    assert resp.status_code == 404


# --- GET /projects/{id}/benchmarks/{run_id} ---


async def test_get_benchmark_with_results(client, session, researcher_user, do_login):
    from app.db.models import BenchmarkResult

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    run = BenchmarkRun(
        project_id=uuid.UUID(project_id),
        models_tested=["Kev-4B"],
        status=BenchmarkStatus.COMPLETED,
    )
    session.add(run)
    await session.commit()
    session.add(
        BenchmarkResult(
            benchmark_run_id=run.id,
            model_name="Kev-4B",
            execution_time_seconds=1.5,
            accuracy=0.9,
            precision_score=0.8,
            recall_score=0.8,
            f1_score=0.8,
        )
    )
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}/benchmarks/{run.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert len(data["results"]) == 1
    assert data["results"][0]["model_name"] == "Kev-4B"
    assert data["results"][0]["f1_score"] == 0.8


async def test_get_benchmark_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    resp = await client.get(f"/api/v1/projects/{project_id}/benchmarks/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_benchmark_other_project_404(client, session, researcher_user, do_login):
    """Run from another project → 404 (no cross-project leak)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    other = await _make_user(session, "other@lab.fr", UserRole.RESEARCHER, "other-pass-123")
    other_project = Project(title="Other", created_by=other.id)
    session.add(other_project)
    await session.commit()
    run = BenchmarkRun(
        project_id=other_project.id, models_tested=["x"], status=BenchmarkStatus.COMPLETED
    )
    session.add(run)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}/benchmarks/{run.id}")
    assert resp.status_code == 404


async def test_cancel_benchmark(client, session, researcher_user, do_login, monkeypatch):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _create_project(client)
    prompt_id, model_name = await _seed_benchmark_prereqs(session, project_id)
    _mock_benchmark_task(monkeypatch)
    base = f"/api/v1/projects/{project_id}/benchmarks"
    run_id = (
        await client.post(base, json={"prompt_template_id": prompt_id, "models": [model_name]})
    ).json()["run_id"]
    r = await client.post(f"{base}/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "FAILED"
    assert (await client.post(f"{base}/{run_id}/cancel")).status_code == 409  # déjà arrêté
    assert (await client.post(f"{base}/{uuid.uuid4()}/cancel")).status_code == 404
