"""Spec 2026-11-20-analyses-runs-1-backend: runs named / overwritable / deletable,
results per run, end of the default search auto-creation. 100% offline."""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import (
    Article,
    ArticlePrediction,
    GoldArticle,
    HumanAnnotation,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    ModelShare,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
    Search,
    User,
    UserRole,
)
from app.searches import definition_hash

FIELDS = [{"key": "ai", "label": "AI", "type": "boolean"}]
PREFIX = "261120"  # launch day frozen by _freeze_date (T2)


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


async def _frozen(client, session, researcher_user, n_articles: int = 2):
    """FROZEN project: 2 articles, a prompt, a usable model, a default search."""
    resp = await client.post("/api/v1/projects", json={"title": "P1"})
    assert resp.status_code == 201, resp.text
    project = await session.get(Project, uuid.UUID(resp.json()["id"]))
    project.status = ProjectStatus.FROZEN
    await session.commit()
    arts = []
    for i in range(1, n_articles + 1):
        a = Article(project_id=project.id, pmid=str(i), title=f"A{i}")
        session.add(a)
        arts.append(a)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Article Text:\n{methods_text}",
    )
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add_all([prompt, model])
    await session.flush()
    session.add(ModelShare(model_id=model.id, project_id=project.id))
    await session.commit()
    s = Search(
        project_id=project.id,
        name=f"{PREFIX}-1",
        question="Q ?",
        fields=FIELDS,
        definition_hash=definition_hash("Q ?", FIELDS),
        is_default=True,
        lot_all=True,
    )
    session.add(s)
    await session.commit()
    return project, str(s.id), str(prompt.id), arts


def _freeze_date(monkeypatch) -> None:
    """Deterministic auto names (R2): the launch day is fixed to 2026-11-20 (UTC)."""
    import app.api.v1.inference as inf

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 20, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(inf, "datetime", _DT)


def _mock_inference_task(monkeypatch, state: str = "PENDING"):
    result = MagicMock()
    result.id = "inf-123"
    result.state = state
    delay = MagicMock(return_value=result)
    import app.tasks.inference as inf_tasks

    monkeypatch.setattr(inf_tasks.inference_task, "delay", delay)
    return delay


async def _trigger(client, project, search_id: str, **extra):
    return await client.post(
        f"/api/v1/projects/{project.id}/inference",
        json={"model": "Kev-4B", "search_id": search_id, **extra},
    )


# --- T1-T6: trigger with a named output / overwrite / frozen hash ---


async def test_t1_trigger_named_run(client, session, researcher_user, do_login, monkeypatch):
    """T1: trigger with a name -> the job is named, the response carries it."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, prompt_id, _ = await _frozen(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    r = await _trigger(client, project, s1, prompt_template_id=prompt_id, name="Alpha")
    assert r.status_code == 202, r.text
    assert r.json()["name"] == "Alpha"
    job = await session.get(InferenceJob, uuid.UUID(r.json()["job_id"]))
    assert job.name == "Alpha"


async def test_t2_auto_name_scoped_to_the_search(
    client, session, researcher_user, do_login, monkeypatch
):
    """T2: no name -> YYMMDD-n per search: another search already holding
    "261120-1" does not collide; the second run takes "-2"."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    _freeze_date(monkeypatch)
    project, s1, prompt_id, _ = await _frozen(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    s2 = Search(project_id=project.id, name=f"{PREFIX}-9", lot_all=True)
    other = InferenceJob(
        project_id=project.id,
        search_id=None,  # placeholder: set after s2 flush
        model_used="Kev-4B",
        name=f"{PREFIX}-1",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add_all([s2, other])
    await session.flush()
    other.search_id = s2.id
    await session.commit()
    r1 = await _trigger(client, project, s1, prompt_template_id=prompt_id)
    assert r1.status_code == 202, r1.text
    assert r1.json()["name"] == f"{PREFIX}-1"  # the other search's name does not count
    job1 = await session.get(InferenceJob, uuid.UUID(r1.json()["job_id"]))
    job1.status = InferenceJobStatus.COMPLETED
    await session.commit()
    r2 = await _trigger(client, project, s1, prompt_template_id=prompt_id)
    assert r2.status_code == 202, r2.text
    assert r2.json()["name"] == f"{PREFIX}-2"


async def test_t3_name_taken_in_the_search_409(client, session, researcher_user, do_login):
    """T3: a name already taken in the search -> 409 (API-level uniqueness, R1)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, prompt_id, _ = await _frozen(client, session, researcher_user)
    session.add(
        InferenceJob(
            project_id=project.id,
            search_id=uuid.UUID(s1),
            model_used="Kev-4B",
            name="Alpha",
            status=InferenceJobStatus.COMPLETED,
        )
    )
    await session.commit()
    r = await _trigger(client, project, s1, prompt_template_id=prompt_id, name="Alpha")
    assert r.status_code == 409
    assert r.json()["detail"] == "run name already taken"


async def test_t4_overwrite_reuses_name_and_deletes_the_target(
    client, session, researcher_user, do_login, monkeypatch
):
    """T4: overwrite -> the new run takes the target's name, re-prompts on the
    target's prompt + options, PENDING; the target and its predictions are gone."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, prompt_id, arts = await _frozen(client, session, researcher_user)
    _mock_inference_task(monkeypatch)
    target = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        prompt_template_id=uuid.UUID(prompt_id),
        model_used="OldMdl",
        options={"gold_examples": 3, "validate": True},
        name="Beta",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add(target)
    await session.flush()
    session.add(
        ArticlePrediction(
            article_id=arts[0].id,
            search_id=uuid.UUID(s1),
            inference_job_id=target.id,
            model_used="OldMdl",
            decision_json={"ai": True},
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    r = await _trigger(
        client, project, s1, prompt_template_id=prompt_id, overwrite_job_id=str(target.id)
    )
    assert r.status_code == 202, r.text
    assert r.json()["name"] == "Beta"
    new = await session.get(InferenceJob, uuid.UUID(r.json()["job_id"]))
    assert new.status == InferenceJobStatus.PENDING
    assert new.model_used == "Kev-4B"
    assert new.prompt_template_id == uuid.UUID(prompt_id)
    assert new.options == {"gold_examples": 3, "validate": True}
    assert await session.get(InferenceJob, target.id) is None
    assert (await session.execute(select(ArticlePrediction))).scalar_one_or_none() is None


async def test_t5_overwrite_guards(client, session, researcher_user, do_login, monkeypatch):
    """T5: overwrite of an active target -> 409; of another search's run -> 404;
    with a name -> 422."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    _mock_inference_task(monkeypatch)
    project, s1, prompt_id, _ = await _frozen(client, session, researcher_user)
    target = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="Kev-4B",
        name="P",
        status=InferenceJobStatus.PENDING,
    )
    session.add(target)
    await session.commit()
    r = await _trigger(
        client, project, s1, prompt_template_id=prompt_id, overwrite_job_id=str(target.id)
    )
    assert r.status_code == 409
    target.status = InferenceJobStatus.COMPLETED
    await session.commit()
    s2 = Search(project_id=project.id, name="B", lot_all=True)
    foreign = InferenceJob(
        project_id=project.id, model_used="Kev-4B", status=InferenceJobStatus.COMPLETED
    )
    session.add_all([s2, foreign])
    await session.flush()
    foreign.search_id = s2.id
    await session.commit()
    r = await _trigger(
        client, project, s1, prompt_template_id=prompt_id, overwrite_job_id=str(foreign.id)
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "job not in this search"
    r = await _trigger(
        client, project, s1, prompt_template_id=prompt_id, overwrite_job_id=str(target.id), name="X"
    )
    assert r.status_code == 422


async def test_t6_definition_hash_frozen_at_creation(
    client, session, researcher_user, do_login, monkeypatch
):
    """T6: the job's definition_hash is the search's at creation; editing the
    corpus afterwards does not move it (R3)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    _mock_inference_task(monkeypatch)
    project, s1, prompt_id, _ = await _frozen(client, session, researcher_user)
    s = await session.get(Search, uuid.UUID(s1))
    before = s.definition_hash
    r = await _trigger(client, project, s1, prompt_template_id=prompt_id)
    assert r.status_code == 202, r.text
    job = await session.get(InferenceJob, uuid.UUID(r.json()["job_id"]))
    assert job.definition_hash == before
    s.question = "Autre question ?"
    s.definition_hash = definition_hash(s.question, FIELDS)
    await session.commit()
    assert job.definition_hash == before != s.definition_hash


# --- T7-T8: rerun with output, run deletion ---


async def test_t7_rerun_overwrite_and_auto_name(
    client, session, researcher_user, do_login, monkeypatch
):
    """T7: rerun with overwrite_job_id re-prompts on the target (same as T4 via
    the rerun endpoint); rerun without args auto-names (R2) — and a legacy job
    without a name stays NULL (no retroactivity)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    _freeze_date(monkeypatch)
    _mock_inference_task(monkeypatch)
    project, s1, prompt_id, arts = await _frozen(client, session, researcher_user)
    last = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        prompt_template_id=uuid.UUID(prompt_id),
        model_used="OldMdl",
        options={"gold_examples": 1, "validate": False},
        name="Beta",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add(last)
    await session.flush()
    session.add(
        ArticlePrediction(
            article_id=arts[0].id,
            search_id=uuid.UUID(s1),
            inference_job_id=last.id,
            model_used="OldMdl",
            decision_json={"ai": False},
            status=PredictionStatus.AUTO_REJECTED,
        )
    )
    await session.commit()
    r = await client.post(
        f"/api/v1/projects/{project.id}/searches/{s1}/rerun",
        json={"model": "Kev-4B", "overwrite_job_id": str(last.id)},
    )
    assert r.status_code == 202, r.text
    assert r.json()["name"] == "Beta"
    new = await session.get(InferenceJob, uuid.UUID(r.json()["job_id"]))
    assert new.status == InferenceJobStatus.PENDING and new.model_used == "Kev-4B"
    assert new.prompt_template_id == uuid.UUID(prompt_id)
    assert new.options == {"gold_examples": 1, "validate": False}
    assert await session.get(InferenceJob, last.id) is None
    assert (await session.execute(select(ArticlePrediction))).scalar_one_or_none() is None
    new.status = InferenceJobStatus.COMPLETED
    await session.commit()
    r2 = await client.post(
        f"/api/v1/projects/{project.id}/searches/{s1}/rerun", json={"model": "Kev-4B"}
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["name"] == f"{PREFIX}-1"  # "Beta" is gone: the smallest free rank
    # a legacy run without a name is never given one retroactively
    legacy = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="OldMdl",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add(legacy)
    await session.commit()
    assert (await session.get(InferenceJob, legacy.id)).name is None


async def test_t8_delete_run(client, session, researcher_user, do_login):
    """T8: DELETE a run -> job + predictions gone, the other runs intact, the
    search-level re-reads and gold untouched; 409 active; 404 foreign (R8)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, _, arts = await _frozen(client, session, researcher_user)
    j1 = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="M1",
        name="r1",
        status=InferenceJobStatus.COMPLETED,
    )
    j2 = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="M2",
        name="r2",
        status=InferenceJobStatus.COMPLETED,
    )
    j3 = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="M3",
        status=InferenceJobStatus.PENDING,
    )
    session.add_all([j1, j2, j3])
    await session.flush()
    session.add_all(
        [
            ArticlePrediction(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                inference_job_id=j1.id,
                model_used="M1",
                decision_json={"ai": True},
                status=PredictionStatus.AUTO_ACCEPTED,
            ),
            ArticlePrediction(
                article_id=arts[1].id,
                search_id=uuid.UUID(s1),
                inference_job_id=j2.id,
                model_used="M2",
                decision_json={"ai": False},
                status=PredictionStatus.AUTO_REJECTED,
            ),
            HumanAnnotation(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                user_id=researcher_user.id,
                decision_json={"ai": True},
            ),
            GoldArticle(
                project_id=project.id,
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                ground_truth_json={"ai": True},
            ),
        ]
    )
    await session.commit()
    base = f"/api/v1/projects/{project.id}/searches/{s1}/jobs"
    assert (await client.delete(f"{base}/{j3.id}")).status_code == 409  # active
    s2 = Search(project_id=project.id, name="B", lot_all=True)
    j4 = InferenceJob(project_id=project.id, model_used="M4", status=InferenceJobStatus.COMPLETED)
    session.add_all([s2, j4])
    await session.flush()
    j4.search_id = s2.id
    await session.commit()
    assert (await client.delete(f"{base}/{j4.id}")).status_code == 404  # another search
    assert (await client.delete(f"{base}/{j1.id}")).status_code == 204
    assert await session.get(InferenceJob, j1.id) is None
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert [p.inference_job_id for p in preds] == [j2.id]  # the other run's prediction intact
    assert (await session.execute(select(HumanAnnotation))).scalar_one()  # re-read intact
    assert (await session.execute(select(GoldArticle))).scalar_one()  # gold intact


# --- T9-T10: the runs API and the results per run ---


async def test_t9_jobs_payload_has_name_hash_and_breakdown(
    client, session, researcher_user, do_login
):
    """T9: GET .../jobs -> name, definition_hash, n_predictions, breakdown
    (exact values on a 2-article fixture; NULL status = "NONE")."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, _, arts = await _frozen(client, session, researcher_user)
    j1 = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="M1",
        name="r1",
        definition_hash="h" * 32,
        status=InferenceJobStatus.COMPLETED,
    )
    j2 = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="M2",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add_all([j1, j2])
    await session.flush()
    session.add_all(
        [
            ArticlePrediction(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                inference_job_id=j1.id,
                model_used="M1",
                decision_json={"ai": True},
                status=PredictionStatus.AUTO_ACCEPTED,
            ),
            ArticlePrediction(
                article_id=arts[1].id,
                search_id=uuid.UUID(s1),
                inference_job_id=j1.id,
                model_used="M1",
                decision_json={"ai": False},
                status=PredictionStatus.NEEDS_HUMAN_REVIEW,
            ),
            ArticlePrediction(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                inference_job_id=j2.id,
                model_used="M2",
                decision_json={"ai": True},
            ),
        ]
    )
    await session.flush()
    # a legacy prediction with a NULL status (Core: the ORM would keep the default)
    await session.execute(
        update(ArticlePrediction)
        .where(ArticlePrediction.inference_job_id == j2.id)
        .values(status=None)
    )
    await session.commit()
    jobs = {
        j["id"]: j
        for j in (await client.get(f"/api/v1/projects/{project.id}/searches/{s1}/jobs")).json()[
            "jobs"
        ]
    }
    a, b = jobs[str(j1.id)], jobs[str(j2.id)]
    assert a["name"] == "r1" and a["definition_hash"] == "h" * 32
    assert a["n_predictions"] == 2 and a["breakdown"] == {
        "AUTO_ACCEPTED": 1,
        "NEEDS_HUMAN_REVIEW": 1,
    }
    assert b["name"] is None and b["definition_hash"] is None  # legacy run
    assert b["n_predictions"] == 1 and b["breakdown"] == {"NONE": 1}


async def test_t10_articles_by_run(client, session, researcher_user, do_login):
    """T10: ?run= -> the list shows that run's decisions; a run from another
    search -> 404; without run -> the search's last prediction (R7)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, _, arts = await _frozen(client, session, researcher_user)
    jA = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="A",
        status=InferenceJobStatus.COMPLETED,
    )
    jB = InferenceJob(
        project_id=project.id,
        search_id=uuid.UUID(s1),
        model_used="B",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add_all([jA, jB])
    await session.flush()
    session.add_all(
        [
            ArticlePrediction(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                inference_job_id=jA.id,
                model_used="A",
                decision_json={"ai": True},
                status=PredictionStatus.AUTO_ACCEPTED,
                created_at=datetime(2026, 11, 1, tzinfo=UTC),
            ),
            ArticlePrediction(
                article_id=arts[0].id,
                search_id=uuid.UUID(s1),
                inference_job_id=jB.id,
                model_used="B",
                decision_json={"ai": False},
                status=PredictionStatus.AUTO_REJECTED,
                created_at=datetime(2026, 11, 2, tzinfo=UTC),  # the later run
            ),
        ]
    )
    await session.commit()
    base = f"/api/v1/projects/{project.id}/articles"

    def decision_of(r, aid) -> dict:
        return next(i for i in r.json()["items"] if i["id"] == str(aid))["model_json"]

    rA = await client.get(base, params={"search": s1, "run": str(jA.id)})
    assert rA.status_code == 200, rA.text
    assert decision_of(rA, arts[0].id) == {"ai": True}  # run A's decision
    assert rA.json()["stats"]["AUTO_ACCEPTED"] == 1  # stats filtered on the run too
    rB = await client.get(base, params={"search": s1, "run": str(jB.id)})
    assert decision_of(rB, arts[0].id) == {"ai": False}  # run B's decision
    # a run of another search -> 404
    s2 = Search(project_id=project.id, name="B", lot_all=True)
    jC = InferenceJob(project_id=project.id, model_used="C", status=InferenceJobStatus.COMPLETED)
    session.add_all([s2, jC])
    await session.flush()
    jC.search_id = s2.id
    await session.commit()
    r404 = await client.get(base, params={"search": s1, "run": str(jC.id)})
    assert r404.status_code == 404
    # without run: the search's last prediction (unchanged behavior)
    r0 = await client.get(base, params={"search": s1})
    assert r0.status_code == 200
    assert decision_of(r0, arts[0].id) == {"ai": False}


# --- T11-T13: no auto-creation, 404 without search, lock scope ---


async def test_t11_new_project_has_no_auto_search(client, session, researcher_user, do_login):
    """T11: a new project starts with an EMPTY list of searches (R9)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post("/api/v1/projects", json={"title": "Neuf"})
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    items = (await client.get(f"/api/v1/projects/{project_id}/searches")).json()["items"]
    assert items == []
    assert (await session.execute(select(Search))).scalars().all() == []


async def test_t12_search_none_404_without_a_default(client, session, researcher_user, do_login):
    """T12: an endpoint resolving search=None on a project without search -> 404
    (nothing is created); a legacy NULL gold row -> the default search IS created
    (house of the NULL data, R10)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.post("/api/v1/projects", json={"title": "Sans corpus"})
    project = await session.get(Project, uuid.UUID(r.json()["id"]))
    session.add(Article(project_id=project.id, pmid="1", title="A1"))
    await session.commit()
    r2 = await client.get(f"/api/v1/projects/{project.id}/articles")
    assert r2.status_code == 404
    assert r2.json()["detail"] == "no default search in this project"
    assert (
        await session.execute(select(Search).where(Search.project_id == project.id))
    ).scalars().all() == []
    # a project with a legacy NULL gold row: the default search is its house
    r3 = await client.post("/api/v1/projects", json={"title": "Legacy gold"})
    p2 = await session.get(Project, uuid.UUID(r3.json()["id"]))
    a2 = Article(project_id=p2.id, pmid="2", title="B1")
    session.add(a2)
    await session.flush()
    session.add(
        GoldArticle(
            project_id=p2.id, article_id=a2.id, search_id=None, ground_truth_json={"ai": True}
        )
    )
    await session.commit()
    r4 = await client.get(f"/api/v1/projects/{p2.id}/articles")
    assert r4.status_code == 200, r4.text
    rows = (await session.execute(select(Search).where(Search.project_id == p2.id))).scalars().all()
    assert len(rows) == 1 and rows[0].is_default and rows[0].lot_all


async def test_t13_question_locked_only_for_the_default_search(
    client, session, researcher_user, do_login
):
    """T13: editing the question of an explicit search WITH results -> 200 (hash
    updated, the runs keep their frozen hash); on the default search -> 409
    question_locked (legacy behavior, R11)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, s1, _, arts = await _frozen(client, session, researcher_user)
    s_def = await session.get(Search, uuid.UUID(s1))
    s2 = Search(
        project_id=project.id,
        name="Corpus",
        question="Q ?",
        fields=FIELDS,
        definition_hash=s_def.definition_hash,
        lot_all=True,
    )
    j1 = InferenceJob(
        project_id=project.id,
        model_used="M",
        name="r",
        definition_hash=s_def.definition_hash,
        status=InferenceJobStatus.COMPLETED,
    )
    session.add_all([s2, j1])
    await session.flush()
    j1.search_id = s2.id
    session.add(
        ArticlePrediction(
            article_id=arts[0].id,
            search_id=s2.id,
            inference_job_id=j1.id,
            model_used="M",
            decision_json={"ai": True},
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    r = await client.patch(
        f"/api/v1/projects/{project.id}", params={"search": s2.id}, json={"question": "Autre ?"}
    )
    assert r.status_code == 200, r.text
    s2r = await session.get(Search, s2.id)
    assert s2r.definition_hash == definition_hash("Autre ?", FIELDS)
    assert (
        await session.get(InferenceJob, j1.id)
    ).definition_hash == s_def.definition_hash  # frozen
    # the default search, with results: the legacy 409 is kept
    session.add(
        ArticlePrediction(
            article_id=arts[0].id,
            search_id=uuid.UUID(s1),
            model_used="M",
            decision_json={"ai": True},
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    r = await client.patch(
        f"/api/v1/projects/{project.id}", params={"search": s1}, json={"question": "Toujours ?"}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "question_locked"
