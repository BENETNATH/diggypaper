"""Searches: several questions per project, batches derived from another search's results,
results / re-reads / analyses attached to each one."""

import re
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
    """LEAD project with 4 articles; initial search: ai=yes for A1, A2; no for A3, A4."""
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
    assert re.fullmatch(r"\d{6}-1", items[0]["name"])  # AAMMJJ-n, pas de libellé français


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
    parent = (await client.get(f"{base}/searches")).json()["items"][0]["name"]
    assert s["lot_size"] == 2 and s["predicted"] == 0 and s["parent"]["name"] == parent
    # no name: AAMMJJ-n, next n in the project
    auto = await client.post(f"{base}/searches", json={"selection": sel})
    assert auto.status_code == 201 and auto.json()["name"] == parent[:7] + "2"
    assert s["question"] == "Quel outil ?" and s["parent_spec"] == {"answers": {"ai": True}}
    # a search's list = its batch; the other's results do not mix in
    assert await titles(client, project, search=s["id"]) == ["A1", "A2"]
    assert await titles(client, project) == ["A1", "A2", "A3", "A4"]
    items = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
    assert all(i["prediction_status"] is None for i in items)  # aucune prédiction ici
    # a re-read in the new search does not touch the initial search
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
    # empty selection: refused
    r = await client.post(
        f"{base}/searches", json={"name": "X", "selection": {"ids": [str(uuid.uuid4())]}}
    )
    assert r.status_code == 422
    # 'confidence' bounded
    sel = {"confidence_min": 0.5}
    assert (await client.post(f"{base}/searches/preview", json=sel)).json() == {"count": 2}
    # deletion: the initial search is protected
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
    # a search from another project is not reachable from this one
    from app.searches import default_search

    foreign = await default_search(session, other)
    await session.commit()
    assert (
        await client.get(f"{base}/articles", params={"search": str(foreign.id)})
    ).status_code == 404


async def test_inference_runs_only_on_the_search_lot(engine, session, corpus, fake_redis):
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
    res = await _run_inference(
        str(job.id), session_factory=factory, inference=Fake(), redis=fake_redis
    )
    assert res["processed"] == 2 and Fake.calls == 3  # 1 warm-up + 2 articles du lot, pas les 4
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
    # the initial search's results are intact (4 predictions without search_id)
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
    # re-read of A1 (corrected: the model said yes, the re-reader says no), then of A2 (validated)
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": False})
    await client.post(f"{base}/articles/{arts[1].id}/annotation", json={"ai": True})
    r = await client.post(f"{base}/gold/from-reviews", json={})
    assert r.status_code == 201 and r.json() == {"added": 2, "updated": 0, "skipped": 0}
    # idempotent: no duplicate
    assert (await client.post(f"{base}/gold/from-reviews", json={})).json()["added"] == 0
    items = (await client.get(f"{base}/gold")).json()["items"]
    by = {i["article_title"]: i for i in items}
    assert by["A1"]["ground_truth_json"]["ai"] is False
    src = by["A1"]["source"]
    assert src["status"] == "CORRECTED" and src["by"] and src["annotation_id"] and not src["blind"]
    assert by["A2"]["source"]["status"] == "VALIDATED" and not by["A1"]["stale"]
    # the re-read then changes: the gold stays frozen but flags itself as stale
    import asyncio

    await asyncio.sleep(1.1)  # SQLite : created_at à la seconde
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": True})
    by = {i["article_title"]: i for i in (await client.get(f"{base}/gold")).json()["items"]}
    assert by["A1"]["ground_truth_json"]["ai"] is False and by["A1"]["stale"] is True
    r = await client.post(f"{base}/gold/from-reviews", json={"refresh": True})
    assert r.json()["updated"] == 2
    by = {i["article_title"]: i for i in (await client.get(f"{base}/gold")).json()["items"]}
    assert by["A1"]["ground_truth_json"]["ai"] is True and not by["A1"]["stale"]
    # remove a reference
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
    # a prediction exists in this search: invisible before re-reading
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
    # once merged into the gold, the re-read is marked "blind"
    await client.post(f"{base}/gold/from-reviews", json={}, params={"search": s["id"]})
    item = (await client.get(f"{base}/gold", params={"search": s["id"]})).json()["items"][0]
    assert item["source"]["blind"] is True


# --- correctifs de l'audit ---------------------------------------------------------------


async def _member(session, project, email, role=ProjectMemberRole.ANNOTATOR):
    from app.db.models import UserRole
    from tests.conftest import _make_user

    u = await _make_user(session, email, UserRole.RESEARCHER, "member-pass-123", True)
    session.add(ProjectMember(project_id=project.id, user_id=u.id, role_in_project=role))
    await session.commit()
    return u


async def _blind_search(client, project, n=4):
    base = f"/api/v1/projects/{project.id}"
    return (
        await client.post(
            f"{base}/searches",
            json={"name": "aveugle", "kind": "blind", "selection": {"sample_n": n, "seed": 1}},
        )
    ).json()


async def test_blind_search_hides_model_and_other_reviewers_everywhere(
    client, session, corpus, do_login
):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    s = await _blind_search(client, project)
    sid = __import__("uuid").UUID(s["id"])
    for a in arts:  # l'IA a répondu dans cette recherche
        session.add(
            ArticlePrediction(
                article_id=a.id,
                search_id=sid,
                model_used="m",
                decision_json={"ai": True},
                confidence_score=0.9,
                status=PredictionStatus.AUTO_ACCEPTED,
            )
        )
    await session.commit()
    # the LEAD re-reads A1; an annotator sees NOTHING: no AI, no LEAD answer
    await client.post(
        f"{base}/articles/{arts[0].id}/annotation", params={"search": s["id"]}, json={"ai": False}
    )
    await _member(session, project, "ann@lab.fr")
    await do_login("ann@lab.fr", "member-pass-123", consent=True)
    items = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
    assert items and all(i["answers"] == [] and i["prediction_status"] is None for i in items)
    assert all(i["confidence"] is None and i["review"] is None for i in items)
    # filters and selections are not an oracle
    assert (
        await client.get(f"{base}/articles", params={"search": s["id"], "answers": '{"ai": true}'})
    ).status_code == 422
    r = await client.get(f"{base}/articles", params={"search": s["id"], "status": "AUTO_ACCEPTED"})
    assert r.json()["items"] == []
    r = await client.post(
        f"{base}/searches/preview", json={"from_search_id": s["id"], "answers": {"ai": True}}
    )
    assert r.status_code == 422
    # another's re-read is not pre-filled
    got = await client.get(f"{base}/articles/{arts[0].id}/annotation", params={"search": s["id"]})
    assert got.json() is None
    # after THEIR re-read, the AI becomes visible for this article
    await client.post(
        f"{base}/articles/{arts[0].id}/annotation", params={"search": s["id"]}, json={"ai": True}
    )
    mine = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
    shown = [i for i in mine if i["answers"]]
    assert len(shown) == 1 and shown[0]["prediction_status"] == "AUTO_ACCEPTED"


async def test_blind_list_shows_only_the_callers_own_review(
    client, session, corpus, do_login, researcher_user
):
    """Aveugle : une fois SA relecture faite, la liste projette SA relecture, pas la dernière
    en date d'un autre membre (fuite review / answers / confidence / by)."""
    import asyncio

    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    s = await _blind_search(client, project)
    article_url = f"{base}/articles/{arts[0].id}"
    aid = str(arts[0].id)

    async def annotate(ai: bool, conf: float):
        r = await client.post(
            f"{article_url}/annotation",
            params={"search": s["id"]},
            json={"ai": ai, "confidence_score": conf},
        )
        assert r.status_code == 201, r.text

    async def listed() -> dict:
        items = (await client.get(f"{base}/articles", params={"search": s["id"]})).json()["items"]
        return next(i for i in items if i["id"] == aid)

    # A (LEAD) relit d'abord, puis B relit le même article plus tard
    await annotate(False, 0.2)
    b = await _member(session, project, "b@lab.fr")
    await _member(session, project, "c@lab.fr")
    await do_login("b@lab.fr", "member-pass-123", consent=True)
    await asyncio.sleep(1.1)  # created_at à la seconde : B est bien « la plus récente »
    await annotate(True, 0.8)

    # B voit SA relecture
    mine_b = await listed()
    assert mine_b["review"]["by"] == (b.full_name or b.email)
    assert mine_b["confidence"] == 0.8
    assert [x["value"] for x in mine_b["answers"] if x["key"] == "ai"] == [True]

    # A voit SA relecture, pas celle de B (pourtant plus récente)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    mine_a = await listed()
    assert mine_a["review"]["by"] == (researcher_user.full_name or researcher_user.email)
    assert mine_a["confidence"] == 0.2
    assert [x["value"] for x in mine_a["answers"] if x["key"] == "ai"] == [False]
    assert mine_a["annotated"] is True

    # C n'a rien relu : aucune relecture (ni A ni B) ne lui est montrée
    await do_login("c@lab.fr", "member-pass-123", consent=True)
    none_c = await listed()
    assert none_c["review"] is None and none_c["answers"] == []
    assert none_c["confidence"] is None and none_c["prediction_status"] is None


async def test_only_lead_or_author_can_refresh_a_gold_reference(client, session, corpus, do_login):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": True})
    await client.post(f"{base}/gold/from-reviews", json={})  # ajouté par le LEAD
    await _member(session, project, "ann2@lab.fr")
    await do_login("ann2@lab.fr", "member-pass-123", consent=True)
    import asyncio

    await asyncio.sleep(1.1)
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": False})
    r = await client.post(f"{base}/gold/from-reviews", json={"refresh": True})
    assert r.json()["updated"] == 0  # pas la référence d'un autre
    g = (await client.get(f"{base}/gold")).json()["items"][0]
    assert g["ground_truth_json"]["ai"] is True


async def test_deleting_a_search_keeps_the_gold_shared_with_the_initial_one(
    client, session, corpus
):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    child = (
        await client.post(
            f"{base}/searches", json={"name": "enfant", "selection": {"answers": {"ai": True}}}
        )
    ).json()  # même question que l'initiale
    await client.post(
        f"{base}/articles/{arts[0].id}/annotation",
        params={"search": child["id"]},
        json={"ai": True},
    )
    await client.post(f"{base}/gold/from-reviews", params={"search": child["id"]}, json={})
    assert len((await client.get(f"{base}/gold")).json()["items"]) == 1  # visible aussi ici
    assert (await client.delete(f"{base}/searches/{child['id']}")).status_code == 204
    assert len((await client.get(f"{base}/gold")).json()["items"]) == 1  # n'a pas disparu


def test_metrics_tolerate_untyped_model_outputs():
    from app import metrics

    assert metrics.as_bool("false") is False and metrics.as_bool("Oui") is True
    assert metrics.relevant({"confidence_score": "high"}) is False  # pas d'exception
    fields = [{"key": "ai", "label": "AI", "type": "boolean"}]
    gold = {1: {"ai": False, "confidence_score": 0.1}, 2: {"ai": True, "confidence_score": 0.9}}
    preds = {1: {}, 2: {"ai": "false", "confidence_score": "0.2"}}  # 1 : sortie invalide
    s = metrics.score(fields, preds, gold)
    assert s["invalid"] == 1
    rel = s["relevance"]  # l'invalide sur un gold négatif est une ERREUR (faux positif)
    assert (rel["fp"], rel["fn"], rel["tn"]) == (1, 1, 0)
    assert s["items"]["ai"]["n"] == 1 and s["items"]["ai"]["fn"] == 1  # "false" ≠ vrai


async def test_search_counts_ignore_legacy_rows_of_other_projects(client, session, corpus):
    project, arts = corpus
    other = Project(title="Autre", created_by=None)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="9", title="F")
    session.add(foreign)
    await session.commit()
    session.add(
        ArticlePrediction(  # ligne ancienne (search_id NULL) d'un AUTRE projet
            article_id=foreign.id,
            model_used="m",
            decision_json={"ai": True},
            confidence_score=0.9,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    items = (await client.get(f"/api/v1/projects/{project.id}/searches")).json()["items"]
    assert items[0]["predicted"] == 4  # et non 5


async def test_crate_export_of_another_search_is_refused_while_one_runs(client, session, corpus):
    from app.db.models import ExportJob
    from app.searches import default_search

    project, _ = corpus
    base = f"/api/v1/projects/{project.id}"
    other = (
        await client.post(f"{base}/searches", json={"name": "B", "selection": {"sample_n": 2}})
    ).json()
    initial = await default_search(session, project)
    await session.commit()
    session.add(
        ExportJob(project_id=project.id, search_id=initial.id, status="RUNNING", user_id=None)
    )
    await session.commit()
    r = await client.post(f"{base}/export/crate", params={"search": other["id"]})
    assert r.status_code == 409


async def test_overview_gives_global_figures_and_per_search_breakdown(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    await client.post(f"{base}/articles/{arts[0].id}/annotation", json={"ai": True})
    await client.post(f"{base}/gold/from-reviews", json={})
    child = (
        await client.post(
            f"{base}/searches", json={"name": "oui", "selection": {"answers": {"ai": True}}}
        )
    ).json()
    o = (await client.get(f"{base}/overview")).json()
    assert (o["articles"], o["analysed"], o["reviewed"], o["gold"]) == (4, 4, 1, 1)
    init, kid = o["searches"][0], next(s for s in o["searches"] if s["id"] == child["id"])
    assert init["is_default"] and init["breakdown"] == {"AUTO_ACCEPTED": 2, "AUTO_REJECTED": 2}
    assert init["to_review"] == 0
    assert kid["lot_size"] == 2 and kid["breakdown"] == {"NONE": 2} and kid["predicted"] == 0


async def test_quick_text_search_on_title_authors_pmid_and_doi(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    arts[0].title, arts[0].authors = "Deep learning of mouse gait", ["Smith J", "Dupont-Martin A"]
    arts[1].title, arts[1].doi, arts[1].journal = "Rat 100% sure_thing", "10.1/xyz.42", "Nature"
    arts[2].title, arts[2].pmid = "Zebrafish tracking", "5550123"
    await session.commit()

    async def found(q):
        r = await client.get(f"{base}/articles", params={"q": q})
        assert r.status_code == 200, r.text
        return sorted(i["title"] for i in r.json()["items"]), r.json()["total"]

    assert (await found("GAIT"))[0] == ["Deep learning of mouse gait"]  # titre, casse ignorée
    assert (await found("dupont"))[0] == ["Deep learning of mouse gait"]  # auteur
    assert (await found("smith mouse"))[0] == ["Deep learning of mouse gait"]  # mots cumulés (ET)
    assert (await found("smith zebrafish"))[0] == []  # ET : pas de correspondance commune
    assert (await found("10.1/xyz"))[0] == ["Rat 100% sure_thing"]  # DOI
    assert (await found("nature"))[0] == ["Rat 100% sure_thing"]  # journal
    assert (await found("555012"))[0] == ["Zebrafish tracking"]  # PMID
    assert (await found("100%"))[0] == ["Rat 100% sure_thing"]  # % littéral, pas un joker
    assert (await found("sure_t"))[0] == ["Rat 100% sure_thing"]  # _ littéral
    assert (await found("a%"))[0] == []  # pas de joker
    # total (pagination) and combined filters
    assert (await found("   "))[1] == 4  # que des espaces : pas de filtre
    r = await client.get(f"{base}/articles", params={"q": "mouse", "status": "AUTO_ACCEPTED"})
    assert r.json()["total"] == 1
    # usable to derive a search and for references
    n = (await client.post(f"{base}/searches/preview", json={"q": "zebrafish"})).json()
    assert n == {"count": 1}
    ris = await client.get(f"{base}/export.ris", params={"q": "zebrafish"})
    assert ris.text.count("TY  -") == 1


async def test_filter_results_by_full_text_or_abstract_only(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    arts[0].fulltext_source, arts[2].fulltext_source = "pmc", "pdf:upload"
    await session.commit()

    async def total(path, **params):
        r = await client.get(f"{base}/{path}", params=params)
        assert r.status_code == 200, r.text
        return r.json()["total"] if path == "articles" else r.text

    assert await total("articles", fulltext="true") == 2
    assert await total("articles", fulltext="false") == 2
    assert await total("articles") == 4
    assert await total("articles", fulltext="true", status="AUTO_ACCEPTED") == 1  # se combine
    ris = await total("export.ris", fulltext="true")  # les références suivent le filtre
    assert ris.count("TY  - ") == 2


async def test_quick_search_ignores_accents_and_case(client, session, corpus):
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    arts[0].title, arts[0].authors = (
        "Étude du sommeil chez la souris",
        ["Smith J", "Dupont-Martin A"],
    )
    arts[1].title, arts[1].journal = "Ordinary title", "Revue Française d'Épidémiologie"
    await session.commit()

    async def found(q):
        r = await client.get(f"{base}/articles", params={"q": q})
        assert r.status_code == 200, r.text
        return sorted(i["title"] for i in r.json()["items"])

    assert await found("etude SOMMEIL") == ["Étude du sommeil chez la souris"]  # sans accents
    assert await found("Étude") == ["Étude du sommeil chez la souris"]  # avec accents
    assert await found("SMITH dupont") == ["Étude du sommeil chez la souris"]  # auteurs
    assert await found("epidemiologie francaise") == ["Ordinary title"]  # journal
    assert await found("100%") == []  # % et _ restent littéraux


# --- model traceability + version (spec 2026-10-29-tracabilite-modele-version §4) ---


async def test_blind_prediction_model_version_only_after_own_review(client, session, corpus):
    """Blind: neither the prediction nor its model/version before their re-read; afterwards yes."""
    project, arts = corpus
    base = f"/api/v1/projects/{project.id}"
    s = await _blind_search(client, project, n=1)
    lot = await titles(client, project, search=s["id"])
    art = next(a for a in arts if a.title in lot)
    session.add(
        ArticlePrediction(
            article_id=art.id,
            search_id=uuid.UUID(s["id"]),
            model_used="m",
            model_identifier="qwen3:8b",
            model_version="2" * 64,
            decision_json={"ai": True},
            confidence_score=0.9,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    url = f"{base}/articles/{art.id}"
    before = await client.get(f"{url}/prediction", params={"search": s["id"]})
    assert before.json() is None and "2" * 64 not in before.text
    await client.post(f"{url}/annotation", params={"search": s["id"]}, json={"ai": False})
    after = (await client.get(f"{url}/prediction", params={"search": s["id"]})).json()
    assert (after["model_identifier"], after["model_version"]) == ("qwen3:8b", "2" * 64)


async def test_search_summary_last_job_model_version(client, session, corpus):
    """Last analysis of a search: main model AND its version (job snapshot)."""
    project, _ = corpus
    session.add(
        InferenceJob(
            project_id=project.id,
            model_used="Big",
            status=InferenceJobStatus.COMPLETED,
            model_info={
                "main": {
                    "name": "Big",
                    "backend": "OLLAMA",
                    "identifier": "qwen3:8b",
                    "version": "2" * 64,
                },
                "prefilter": None,
            },
        )
    )
    await session.commit()
    items = (await client.get(f"/api/v1/projects/{project.id}/searches")).json()["items"]
    assert items[0]["last_job"]["model"] == "Big"
    assert items[0]["last_job"]["model_version"] == "2" * 64


async def test_blind_search_export_hides_model_identifier_and_version(
    client, session, corpus, tmp_path
):
    """Export of a blind search (XLSX + RO-Crate extraction.csv): model,
    identifier and version columns empty — traceability does not bypass the blind."""
    import csv
    import io
    import zipfile

    from openpyxl import load_workbook

    from app.api.v1.export import write_crate
    from app.db.models import Search

    project, arts = corpus
    s = await _blind_search(client, project)
    sid = __import__("uuid").UUID(s["id"])
    for a in arts:
        session.add(
            ArticlePrediction(
                article_id=a.id,
                search_id=sid,
                model_used="Big",
                model_identifier="qwen3:8b",
                model_version="2" * 64,
                decision_json={"ai": True},
                confidence_score=0.9,
                status=PredictionStatus.AUTO_ACCEPTED,
            )
        )
    await session.commit()

    resp = await client.get(
        f"/api/v1/projects/{project.id}/export.xlsx", params={"search": s["id"]}
    )
    assert resp.status_code == 200
    head, *rows = list(load_workbook(io.BytesIO(resp.content))["Résultats"].values)
    assert rows
    for col in ("Modèle", "Identifiant du modèle", "Version du modèle"):
        assert all(r[head.index(col)] is None for r in rows)

    out = tmp_path / "blind.zip"
    with open(out, "wb") as fh:
        await write_crate(
            session, project, "lead@lab.fr", fh, search=await session.get(Search, sid)
        )
    with zipfile.ZipFile(out) as z:
        csv_rows = list(csv.DictReader(io.StringIO(z.read("extraction.csv").decode("utf-8-sig"))))
    assert csv_rows
    for r in csv_rows:
        assert not (r["model"] or r["model_identifier"] or r["model_version"])
