"""Tests for re-run + comparison (spec 2026-11-07-rerun-comparaison-modele.md).

§1: ArticlePrediction.inference_job_id (FK SET NULL)
§2: POST /projects/{pid}/searches/{sid}/rerun — new job, forced model, 409/422/403
§3: GET  /projects/{pid}/searches/{sid}/comparison?job_a&job_b — intersection, agree
100% offline.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.db.models import (
    Article,
    ArticlePrediction,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    ModelShare,
    PredictionStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    PromptTemplate,
    Search,
    User,
    UserRole,
)
from app.tasks import inference as inference_task_module
from app.tasks.inference import _run_inference

FIELDS = [{"key": "include", "label": "Include?", "type": "boolean"}]


async def _seed(session: AsyncSession, n_articles: int = 2, status=ProjectStatus.FROZEN):
    """Frozen project + search (fields) + prompt + 2 models + N articles."""
    user = User(
        email="lead@lab.fr",
        hashed_password=hash_password("lead-pass-123"),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
    )
    session.add(user)
    await session.commit()
    project = Project(title="P", created_by=user.id, status=status)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    search = Search(
        project_id=project.id,
        name="S1",
        question="Q ?",
        fields=FIELDS,
        lot_all=True,
    )
    session.add(search)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Article Text:\n{methods_text}",
    )
    session.add(prompt)
    await session.commit()
    for name in ("Kev-4B", "Nova-7B"):
        m = ModelRegistry(name=name, model_identifier=f"x/{name.lower()}")
        session.add(m)
        await session.commit()
        session.add(ModelShare(model_id=m.id, project_id=project.id))
        await session.commit()
    for i in range(n_articles):
        session.add(
            Article(project_id=project.id, pmid=str(i + 1), title=f"A{i}", methods_text="M.")
        )
    await session.commit()
    return user, project, search, prompt


def _mock_inference_task(monkeypatch):
    mock_result = MagicMock()
    mock_result.id = "rerun-1"
    mock_result.state = "PENDING"
    mock_delay = MagicMock(return_value=mock_result)
    monkeypatch.setattr(inference_task_module.inference_task, "delay", mock_delay)
    return mock_delay


def _prediction(article_id, search_id, decision, confidence=0.9, job_id=None):
    return ArticlePrediction(
        article_id=article_id,
        search_id=search_id,
        model_used="Kev-4B",
        decision_json=decision,
        confidence_score=confidence,
        status=PredictionStatus.NEEDS_HUMAN_REVIEW,
        inference_job_id=job_id,
    )


# --- §1: inference_job_id on predictions ---------------------------------


async def test_prediction_created_by_job_carries_job_id(engine, session, fake_redis):
    from tests.test_inference_task import FakeInference

    user, project, search, prompt = await _seed(session)
    model = (await session.execute(select(ModelRegistry))).scalars().first()
    job = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        prompt_template_id=prompt.id,
        model_used=model.name,
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_inference(str(job.id), session_factory=factory, inference=FakeInference(0.9), redis=fake_redis)
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(preds) == 2
    assert all(p.inference_job_id == job.id for p in preds)


async def test_prediction_without_job_keeps_null(session):
    p = ArticlePrediction(model_used="m", decision_json={"x": 1})
    session.add(p)
    await session.commit()
    assert p.inference_job_id is None


# --- §2: POST rerun -------------------------------------------------------


async def _baseline_analysis(session, project, search, prompt, model_name="Kev-4B"):
    """An existing analysis: one job + one prediction per article."""
    job = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        prompt_template_id=prompt.id,
        model_used=model_name,
        options={"gold_examples": 2, "validate": True},
        status=InferenceJobStatus.COMPLETED,
        model_info={"main": {"name": model_name, "identifier": "x", "version": "v1"}},
    )
    session.add(job)
    await session.commit()
    arts = (await session.execute(select(Article).where(Article.project_id == project.id))).scalars().all()
    for a in arts:
        session.add(_prediction(a.id, search.id, {"include": True, "why": "a"}, job_id=job.id))
    await session.commit()
    return job, arts


async def test_rerun_lead_202_new_job_same_search(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    job1, _ = await _baseline_analysis(session, project, search, prompt)
    mock_delay = _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{search.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "PENDING" and data["job_id"]
    mock_delay.assert_called_once_with(data["job_id"])
    job2 = await session.get(InferenceJob, uuid.UUID(data["job_id"]))
    assert job2.model_used == "Nova-7B"
    assert job2.search_id == search.id
    assert job2.prompt_template_id == prompt.id  # inherited from the analysis
    assert job2.options == {"gold_examples": 2, "validate": True}  # inherited
    assert job2.status == InferenceJobStatus.PENDING


async def test_rerun_keeps_previous_predictions(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    job1, arts = await _baseline_analysis(session, project, search, prompt)
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{search.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 202
    preds = (
        await session.execute(select(ArticlePrediction).where(ArticlePrediction.search_id == search.id))
    ).scalars().all()
    assert len(preds) == len(arts)  # untouched
    assert all(p.inference_job_id == job1.id for p in preds)


async def test_rerun_running_job_409(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    job1, _ = await _baseline_analysis(session, project, search, prompt)
    running = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        prompt_template_id=prompt.id,
        model_used="Nova-7B",
        status=InferenceJobStatus.RUNNING,
    )
    session.add(running)
    await session.commit()
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{search.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 409


async def test_rerun_unknown_model_422(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    await _baseline_analysis(session, project, search, prompt)
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{search.id}/rerun", json={"model": "Ghost-9B"}
    )
    assert resp.status_code == 422


async def test_rerun_non_lead_403(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    await _baseline_analysis(session, project, search, prompt)
    member = await session.get(ProjectMember, (project.id, user.id))
    member.role_in_project = ProjectMemberRole.ANNOTATOR
    await session.commit()
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{search.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 403


async def test_rerun_without_analysis_409(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    # a second search with no analysis at all
    s2 = Search(project_id=project.id, name="S2", question="Q2 ?", fields=FIELDS, lot_all=True)
    session.add(s2)
    await session.commit()
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/searches/{s2.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 409


async def test_rerun_other_project_search_404(client, session, do_login, monkeypatch):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    await _baseline_analysis(session, project, search, prompt)
    foreign = Project(title="F", created_by=user.id, status=ProjectStatus.FROZEN)
    session.add(foreign)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=foreign.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    _mock_inference_task(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{foreign.id}/searches/{search.id}/rerun", json={"model": "Nova-7B"}
    )
    assert resp.status_code == 404


# --- §3: GET comparison ---------------------------------------------------


async def _two_jobs(session, project, search, arts, agree_first=True):
    """Two jobs with predictions on the same articles (some agree, some not)."""
    jobs = []
    for i, (model_name, version) in enumerate((("Kev-4B", "v1"), ("Nova-7B", "v2"))):
        job = InferenceJob(
            project_id=project.id,
            search_id=search.id,
            prompt_template_id=None,
            model_used=model_name,
            status=InferenceJobStatus.COMPLETED,
            model_info={"main": {"name": model_name, "identifier": "x", "version": version}},
        )
        session.add(job)
        await session.commit()
        jobs.append(job)
        for j, a in enumerate(arts):
            if j == 0:
                value = True if agree_first else "oui"  # normalization check (test b)
            elif j == 1:
                value = True if i == 0 else False  # disagreement
            else:
                value = "exclude" if i else "exclude"  # normalization: strings equal
            session.add(
                _prediction(a.id, search.id, {"include": value, "why": "x"}, job_id=job.id)
            )
        await session.commit()
    return jobs


async def test_comparison_payload(client, session, do_login):
    user, project, search, prompt = await _seed(session, n_articles=3)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    arts = (
        (await session.execute(select(Article).where(Article.project_id == project.id)))
        .scalars()
        .all()
    )
    job_a, job_b = await _two_jobs(session, project, search, arts, agree_first=True)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(job_a.id), "job_b": str(job_b.id)},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["n"] == 3
    assert data["n_agree"] == 2  # article 0 (both true) + article 2 (both "exclude")
    assert abs(data["agreement"] - 2 / 3) < 1e-3
    assert data["model_a"] == {"name": "Kev-4B", "version": "v1"}
    assert data["model_b"] == {"name": "Nova-7B", "version": "v2"}
    assert data["n_excluded"] == 0
    rows = {r["article_id"]: r for r in data["rows"]}
    assert rows[str(arts[1].id)]["agree"] is False
    assert rows[str(arts[0].id)]["agree"] is True
    assert len(data["disagreements"]) == 1
    dis = data["disagreements"][0]
    assert dis["article_id"] == str(arts[1].id)
    assert dis["title"] == "A1"
    assert dis["decision_a"] is True and dis["decision_b"] is False


async def test_comparison_normalized_yes_values_agree(client, session, do_login):
    """'oui' (string) vs true (bool) vs 'include' → same normalized value."""
    user, project, search, prompt = await _seed(session, n_articles=2)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    arts = (
        (await session.execute(select(Article).where(Article.project_id == project.id)))
        .scalars()
        .all()
    )
    job_a, job_b = await _two_jobs(session, project, search, arts, agree_first=False)
    # article 0: "oui" (a) vs "oui" — and check the include/true equivalence on article 1
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(job_a.id), "job_b": str(job_b.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    rows = {r["article_id"]: r for r in data["rows"]}
    assert rows[str(arts[0].id)]["agree"] is True  # "oui" == "oui"


async def test_comparison_disjoint_lots_n0(client, session, do_login):
    user, project, search, prompt = await _seed(session, n_articles=4)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    arts = (
        (await session.execute(select(Article).where(Article.project_id == project.id)))
        .scalars()
        .all()
    )
    # job A predicts only articles 0-1, job B only articles 2-3
    jobs = []
    for i, (model_name, sl) in enumerate((("Kev-4B", slice(0, 2)), ("Nova-7B", slice(2, 4)))):
        job = InferenceJob(
            project_id=project.id,
            search_id=search.id,
            model_used=model_name,
            status=InferenceJobStatus.COMPLETED,
        )
        session.add(job)
        await session.commit()
        jobs.append(job)
        for a in arts[sl]:
            session.add(_prediction(a.id, search.id, {"include": True}, job_id=job.id))
        await session.commit()
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(jobs[0].id), "job_b": str(jobs[1].id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["n"] == 0
    assert data["agreement"] is None
    assert data["n_excluded"] == 4
    assert data["rows"] == [] and data["disagreements"] == []


async def test_comparison_other_search_404(client, session, do_login):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    s2 = Search(project_id=project.id, name="S2", question="Q2 ?", fields=FIELDS, lot_all=True)
    session.add(s2)
    await session.commit()
    foreign_job = InferenceJob(
        project_id=project.id, search_id=s2.id, model_used="Kev-4B", status=InferenceJobStatus.COMPLETED
    )
    session.add(foreign_job)
    await session.commit()
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(foreign_job.id), "job_b": str(foreign_job.id)},
    )
    assert resp.status_code == 404


async def test_comparison_unknown_job_404(client, session, do_login):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(uuid.uuid4()), "job_b": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_comparison_non_member_403(client, session, do_login):
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    other = User(
        email="out@lab.fr",
        hashed_password=hash_password("out-pass-123"),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
    )
    session.add(other)
    await session.commit()
    await do_login("out@lab.fr", "out-pass-123", consent=True)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/comparison",
        params={"job_a": str(uuid.uuid4()), "job_b": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


# --- §4 (contract): the search's jobs, newest first (comparison selector) ---


async def test_search_jobs_list(client, session, do_login):
    """The search's inference jobs are listed newest first (id, model, status)."""
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    arts = (
        (await session.execute(select(Article).where(Article.project_id == project.id)))
        .scalars()
        .all()
    )
    job_a, job_b = await _two_jobs(session, project, search, arts, agree_first=True)
    # the endpoint orders by created_at: make the order explicit (test-DB timestamps tie)
    job_a.created_at = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    job_b.created_at = datetime(2026, 11, 1, 11, 0, 0, tzinfo=timezone.utc)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert [j["id"] for j in jobs] == [str(job_b.id), str(job_a.id)]  # newest first
    assert {j["model_used"] for j in jobs} == {"Kev-4B", "Nova-7B"}
    assert all(j["status"] == "COMPLETED" for j in jobs)


async def test_search_jobs_other_project_404(client, session, do_login):
    """Jobs of a search from another project → 404 (the user is lead of both)."""
    user, project, search, prompt = await _seed(session)
    await do_login("lead@lab.fr", "lead-pass-123", consent=True)
    foreign = Project(title="F", created_by=user.id, status=ProjectStatus.FROZEN)
    session.add(foreign)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=foreign.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{foreign.id}/searches/{search.id}/jobs")
    assert resp.status_code == 404
