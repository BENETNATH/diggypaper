"""Analyses programmées (recherche 2 lancée quand la recherche 1 a fini) + question figée."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from test_searches import corpus  # noqa: F401  (fixture)

from app.db.models import (
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PromptTemplate,
    Search,
    SearchArticle,
)
from app.scheduler import start_ready
from app.searches import default_search


@pytest.fixture
def started(monkeypatch):
    import app.tasks.inference as inf

    delay = MagicMock()
    monkeypatch.setattr(inf.inference_task, "delay", delay)
    return delay


async def _finish_initial_analysis(session, project):
    initial = await default_search(session, project)
    session.add(
        InferenceJob(
            project_id=project.id,
            search_id=initial.id,
            model_used="m",
            status=InferenceJobStatus.COMPLETED,
        )
    )
    await session.commit()
    return initial


async def test_scheduled_search_waits_then_starts_on_results(client, session, corpus, started):  # noqa: F811
    project, _ = corpus
    session.add(ModelRegistry(name="Mdl", model_identifier="x", owner_id=project.created_by))
    await session.commit()
    base = f"/api/v1/projects/{project.id}"
    body = {
        "name": "Suite",
        "selection": {"status": "AUTO_ACCEPTED"},
        "question": "Quel outil ?",
        "schedule": {"model": "Mdl", "instructions": "Only mice."},
    }
    r = await client.post(f"{base}/searches", json=body)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["schedule_state"] == "waiting" and s["lot_size"] == 0 and s["parent"]
    started.assert_not_called()  # la recherche 1 n'a pas fini

    initial = await _finish_initial_analysis(session, project)
    await session.commit()
    job_id = await start_ready(session, project)
    assert job_id and started.call_count == 1
    search = await session.get(Search, __import__("uuid").UUID(s["id"]))
    assert search.schedule_state == "started"
    lot = (
        (await session.execute(select(SearchArticle).where(SearchArticle.search_id == search.id)))
        .scalars()
        .all()
    )
    assert len(lot) == 2  # A1, A2 : acceptés par la recherche initiale
    job = await session.get(InferenceJob, __import__("uuid").UUID(job_id))
    assert job.search_id == search.id and job.model_used == "Mdl"
    prompt = await session.get(PromptTemplate, job.prompt_template_id)
    assert "Quel outil ?" in prompt.system_prompt and prompt.system_prompt.endswith("Only mice.")
    assert initial.id != search.id
    # une seule analyse à la fois : rien d'autre ne part tant que celle-ci tourne
    assert await start_ready(session, project) is None
    act = (await client.get(f"{base}/inference", params={"active": "true"})).json()["job"]
    assert act["id"] == job_id  # le job actif du projet, quelle que soit la recherche affichée


async def test_scheduled_on_same_lot_and_failures(client, session, corpus, started):  # noqa: F811
    project, _ = corpus
    session.add(ModelRegistry(name="Mdl", model_identifier="x", owner_id=project.created_by))
    await session.commit()
    base = f"/api/v1/projects/{project.id}"
    await _finish_initial_analysis(session, project)
    # origine déjà analysée + même lot (aucun critère) : l'analyse démarre tout de suite
    r = await client.post(f"{base}/searches", json={"schedule": {"model": "Mdl"}})
    assert r.status_code == 201 and started.call_count == 1
    assert r.json()["schedule_state"] == "started" and r.json()["lot_size"] == 4
    # sélection vide : la programmation échoue proprement
    session.add(
        InferenceJob(
            project_id=project.id,
            search_id=(await default_search(session, project)).id,
            model_used="m",
            status=InferenceJobStatus.COMPLETED,
        )
    )
    for j in (await session.execute(select(InferenceJob))).scalars():
        j.status = InferenceJobStatus.COMPLETED  # plus rien n'est en cours
    await session.commit()
    r = await client.post(
        f"{base}/searches",
        json={"selection": {"q": "no-such-title"}, "schedule": {"model": "Mdl"}},
    )
    assert (
        r.json()["schedule_state"] == "failed"
        and r.json()["schedule"]["error"] == "empty_selection"
    )
    # modèle inconnu : refusé à la création
    r = await client.post(f"{base}/searches", json={"schedule": {"model": "Nope"}})
    assert r.status_code == 422


async def test_question_is_locked_once_the_search_has_results(client, session, corpus):  # noqa: F811
    project, _ = corpus
    url = f"/api/v1/projects/{project.id}"
    assert (await client.patch(url, json={"question": "Autre ?"})).status_code == 409
    assert (await client.patch(url, json={"text_sections": ["methods"]})).status_code == 200
    # une recherche sans résultat se modifie librement
    new = await client.post(f"{url}/searches", json={"selection": {"status": "AUTO_ACCEPTED"}})
    sid = new.json()["id"]
    ok = await client.patch(f"{url}?search={sid}", json={"question": "Autre ?"})
    assert ok.status_code == 200 and ok.json()["question"] == "Autre ?"


async def test_waiting_search_cannot_be_launched_by_hand(client, session, corpus, started):  # noqa: F811
    project, _ = corpus
    session.add(ModelRegistry(name="Mdl", model_identifier="x", owner_id=project.created_by))
    await session.commit()
    base = f"/api/v1/projects/{project.id}"
    s = (
        await client.post(f"{base}/searches", json={"selection": {}, "schedule": {"model": "Mdl"}})
    ).json()
    assert s["schedule_state"] == "waiting"
    r = await client.post(f"{base}/inference", json={"model": "Mdl", "search_id": s["id"]})
    assert r.status_code == 409
