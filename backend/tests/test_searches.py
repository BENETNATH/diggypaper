"""Recherches : plusieurs questions par projet, lots dérivés des résultats d'une autre recherche,
résultats / relectures / analyses rattachés à chacune."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    PromptTemplate,
)
from app.tasks.inference import _run_inference

FIELDS = [{"key": "ai", "label": "AI", "type": "boolean"}]


@pytest.fixture
async def corpus(session, researcher_user, client, do_login):
    """Projet LEAD avec 4 articles ; recherche initiale : ai=oui pour A1, A2 ; non pour A3, A4."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id, question="Q ?", fields=FIELDS)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    arts = []
    for i in range(1, 5):
        a = Article(project_id=project.id, pmid=str(i), title=f"A{i}")
        session.add(a)
        arts.append(a)
    await session.commit()
    for a, yes in zip(arts, (True, True, False, False), strict=True):
        session.add(
            ArticlePrediction(
                article_id=a.id,
                model_used="m",
                decision_json={"ai": yes},
                confidence_score=0.9 if yes else 0.1,
                status=PredictionStatus.AUTO_ACCEPTED if yes else PredictionStatus.AUTO_REJECTED,
            )
        )
    await session.commit()
    return project, arts


async def titles(client, project, **params):
    r = await client.get(f"/api/v1/projects/{project.id}/articles", params=params)
    assert r.status_code == 200, r.text
    return sorted(i["title"] for i in r.json()["items"])


async def test_default_search_covers_the_whole_corpus(client, corpus):
    project, _ = corpus
    items = (await client.get(f"/api/v1/projects/{project.id}/searches")).json()["items"]
    assert len(items) == 1 and items[0]["is_default"] and items[0]["lot_size"] == 4
    assert items[0]["predicted"] == 4


async def test_derive_search_from_answers_and_scope_results(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    sel = {"answers": {"ai": True}}
    assert (await client.post(f"{base}/searches/preview", json=sel)).json() == {"count": 2}
    r = await client.post(
        f"{base}/searches",
        json={"name": "Détail des oui", "selection": sel, "question": "Quel outil ?"},
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert (
        s["lot_size"] == 2 and s["predicted"] == 0 and s["parent"]["name"] == "Recherche initiale"
    )
    assert s["question"] == "Quel outil ?" and s["parent_spec"] == {"answers": {"ai": True}}
    # la liste d'une recherche = son lot ; les résultats de l'autre ne s'y mélangent pas
    assert await titles(client, project, search=s["id"]) == ["A1", "A2"]
    assert await titles(client, project) == ["A1", "A2", "A3", "A4"]
    items = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
    assert all(i["prediction_status"] is None for i in items)  # aucune prédiction ici
    # une relecture dans la nouvelle recherche ne touche pas la recherche initiale
    r = await client.post(
        f"{base}/articles/{arts[0].id}/annotation", params={"search": s["id"]}, json={"ai": False}
    )
    assert r.status_code == 201
    r0 = (await client.get(f"{base}/articles")).json()["items"]
    assert not any(i["annotated"] for i in r0)
    r1 = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
    assert [i["annotated"] for i in sorted(r1, key=lambda i: i["title"])] == [True, False]


async def test_selection_sample_reviewed_and_errors(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    r = await client.post(f"{base}/searches/preview", json={"sample_n": 3, "seed": 7})
    assert r.json() == {"count": 3}
    a = (
        await client.post(
            f"{base}/searches", json={"name": "S1", "selection": {"sample_n": 3, "seed": 7}}
        )
    ).json()
    b = (
        await client.post(
            f"{base}/searches", json={"name": "S2", "selection": {"sample_n": 3, "seed": 7}}
        )
    ).json()
    assert await titles(client, project, search=a["id"]) == await titles(
        client, project, search=b["id"]
    )
    # sélection vide : refusée
    r = await client.post(
        f"{base}/searches", json={"name": "X", "selection": {"ids": [str(uuid.uuid4())]}}
    )
    assert r.status_code == 422
    # 'confidence' bornée
    sel = {"confidence_min": 0.5}
    assert (await client.post(f"{base}/searches/preview", json=sel)).json() == {"count": 2}
    # suppression : la recherche initiale est protégée
    sid = (await client.get(f"{base}/searches")).json()["items"][0]["id"]
    assert (await client.delete(f"{base}/searches/{sid}")).status_code == 409
    assert (await client.delete(f"{base}/searches/{a['id']}")).status_code == 204


async def test_non_lead_cannot_create_and_foreign_search_is_404(
    client, session, corpus, admin_user
):
    project, _ = corpus
    other = Project(title="Autre", created_by=None)
    session.add(other)
    await session.commit()
    base = f"/api/v1/projects/{project.id}"
    # une recherche d'un autre projet n'est pas atteignable par ce projet
    from app.searches import default_search

    foreign = await default_search(session, other)
    await session.commit()
    assert (
        await client.get(f"{base}/articles", params={"search": str(foreign.id)})
    ).status_code == 404


async def test_inference_runs_only_on_the_search_lot(engine, session, corpus):
    project, arts = corpus
    prompt = PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")
    session.add_all([prompt, ModelRegistry(name="Kev-4B", model_identifier="x")])
    await session.commit()
    from app.api.v1.searches import Selection, select_articles
    from app.db.models import Search, SearchArticle
    from app.searches import definition_hash

    ids = await select_articles(session, project, Selection(answers={"ai": True}))
    s = Search(project_id=project.id, name="oui", question="Q2", fields=FIELDS, is_default=False)
    s.definition_hash = definition_hash(s.question, s.fields)
    session.add(s)
    await session.flush()
    session.add_all(SearchArticle(search_id=s.id, article_id=i) for i in ids)
    job = InferenceJob(
        project_id=project.id,
        search_id=s.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    class Fake:
        calls = 0

        async def infer(self, *a):
            Fake.calls += 1
            return {"ai": True, "confidence_score": 0.9, "evidence_quotes": []}

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    res = await _run_inference(str(job.id), session_factory=factory, inference=Fake())
    assert res["processed"] == 2 and Fake.calls == 2  # 2 articles du lot, pas les 4
    preds = (
        (
            await session.execute(
                select(ArticlePrediction).where(ArticlePrediction.search_id == s.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(preds) == 2
    # les résultats de la recherche initiale sont intacts (4 prédictions sans search_id)
    legacy = (
        (
            await session.execute(
                select(ArticlePrediction).where(ArticlePrediction.search_id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert len(legacy) == 4


async def test_gold_from_reviews_any_member_with_source_link(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    # relecture de A1 (corrigée : le modèle disait oui, le relecteur dit non), puis de A2 (validée)
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": False})
    await client.post(f"{base}/articles/{arts[1].id}/annotation", json={"ai": True})
    r = await client.post(f"{base}/gold/from-reviews", json={})
    assert r.status_code == 201 and r.json() == {"added": 2, "updated": 0, "skipped": 0}
    # idempotent : pas de doublon
    assert (await client.post(f"{base}/gold/from-reviews", json={})).json()["added"] == 0
    items = (await client.get(f"{base}/gold")).json()["items"]
    by = {i["article_title"]: i for i in items}
    assert by["A1"]["ground_truth_json"]["ai"] is False
    src = by["A1"]["source"]
    assert src["status"] == "CORRECTED" and src["by"] and src["annotation_id"] and not src["blind"]
    assert by["A2"]["source"]["status"] == "VALIDATED" and not by["A1"]["stale"]
    # la relecture change ensuite : le gold reste figé mais signale qu'il est obsolète
    import asyncio

    await asyncio.sleep(1.1)  # SQLite : created_at à la seconde
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": True})
    by = {i["article_title"]: i for i in (await client.get(f"{base}/gold")).json()["items"]}
    assert by["A1"]["ground_truth_json"]["ai"] is False and by["A1"]["stale"] is True
    r = await client.post(f"{base}/gold/from-reviews", json={"refresh": True})
    assert r.json()["updated"] == 2
    by = {i["article_title"]: i for i in (await client.get(f"{base}/gold")).json()["items"]}
    assert by["A1"]["ground_truth_json"]["ai"] is True and not by["A1"]["stale"]
    # on retire une référence
    assert (await client.delete(f"{base}/gold/{by['A2']['id']}")).status_code == 204


async def test_gold_is_shared_by_searches_with_the_same_question(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": True})
    await client.post(f"{base}/gold/from-reviews", json={})
    same = (
        await client.post(
            f"{base}/searches",
            json={"name": "même question", "selection": {"answers": {"ai": True}}},
        )
    ).json()
    other = (
        await client.post(
            f"{base}/searches",
            json={
                "name": "autre question",
                "question": "Autre chose ?",
                "selection": {"answers": {"ai": True}},
            },
        )
    ).json()
    golds = {}
    for key, sid in (("same", same["id"]), ("other", other["id"])):
        golds[key] = (await client.get(f"{base}/gold", params={"search": sid})).json()["items"]
    assert len(golds["same"]) == 1  # même définition : la référence vaut aussi ici
    assert golds["other"] == []  # autre question : autre vérité


async def test_blind_sample_hides_predictions_until_reviewed(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    s = (
        await client.post(
            f"{base}/searches",
            json={"name": "Échantillon", "kind": "blind", "selection": {"sample_n": 2, "seed": 1}},
        )
    ).json()
    assert s["kind"] == "blind" and s["lot_size"] == 2
    # une prédiction existe dans cette recherche : invisible avant relecture
    lot = await titles(client, project, search=s["id"])
    art = next(a for a in arts if a.title in lot)
    session.add(
        ArticlePrediction(
            article_id=art.id,
            search_id=__import__("uuid").UUID(s["id"]),
            model_used="m",
            decision_json={"ai": True},
            confidence_score=0.9,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    url = f"{base}/articles/{art.id}"
    assert (await client.get(f"{url}/prediction", params={"search": s["id"]})).json() is None
    r = await client.post(f"{url}/annotation", params={"search": s["id"]}, json={"ai": False})
    assert r.status_code == 201
    assert (await client.get(f"{url}/prediction", params={"search": s["id"]})).json() is not None
    ann = (
        (await session.execute(select(HumanAnnotation).where(HumanAnnotation.article_id == art.id)))
        .scalars()
        .one()
    )
    assert ann.blind is True
    # versée au gold, la relecture est marquée « à l'aveugle »
    await client.post(f"{base}/gold/from-reviews", json={}, params={"search": s["id"]})
    item = (await client.get(f"{base}/gold", params={"search": s["id"]})).json()["items"][0]
    assert item["source"]["blind"] is True
