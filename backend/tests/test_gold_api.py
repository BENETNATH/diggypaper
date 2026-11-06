"""Gold Dataset route tests (app/api/v1/gold.py) — 100% offline."""

from conftest import LEGACY_FIELDS

from app.db.models import Article, HumanAnnotation, Project, ProjectMember, ProjectMemberRole

VALID_GT = {
    "use_ai_behavior": True,
    "animal_sex": "BOTH_SEXES",
    "confidence_score": 0.9,
    "evidence_quotes": ["mice were randomized"],
    "reasoning_summary": "ok",
}


async def _make_project_with_article(
    session, lead_user, fields: list[dict] | None = None
) -> tuple[Project, Article]:
    project = Project(title="P", created_by=lead_user.id, fields=fields)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=lead_user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    article = Article(
        project_id=project.id, pmid="111", title="A study", methods_text="Methods here."
    )
    session.add(article)
    await session.commit()
    return project, article


async def test_create_gold_lead(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["article_title"] == "A study"
    assert body["ground_truth_json"]["use_ai_behavior"] is True


async def test_list_gold(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    resp = await client.get(f"/api/v1/projects/{project.id}/gold")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


async def test_create_gold_missing_key_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    bad_gt = {k: v for k, v in VALID_GT.items() if k != "animal_sex"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": bad_gt},
    )
    assert resp.status_code == 422


async def test_create_gold_article_not_in_project_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    # Article from another project.
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign_article = Article(project_id=other.id, pmid="222", title="Foreign")
    session.add(foreign_article)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(foreign_article.id), "ground_truth_json": VALID_GT},
    )
    assert resp.status_code == 422


async def test_from_reviews_rejects_article_ids_of_another_project(
    client, session, researcher_user, do_login
):
    """An article id from another project (carrying a legacy review,
    ``search_id`` NULL) must never create a reference in the calling project."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign_article = Article(project_id=other.id, pmid="333", title="Foreign")
    session.add(foreign_article)
    await session.commit()
    session.add(
        HumanAnnotation(
            article_id=foreign_article.id,
            user_id=researcher_user.id,
            search_id=None,  # relecture legacy : retenue par in_search() de la recherche initiale
            decision_json={**VALID_GT, "review_status": "MANUAL"},
        )
    )
    await session.commit()
    base = f"/api/v1/projects/{project.id}/gold"
    resp = await client.post(
        f"{base}/from-reviews", json={"article_ids": [str(foreign_article.id)]}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "article not in this project"
    assert (await client.get(base)).json()["items"] == []


async def test_delete_gold_lead(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    gid = (
        await client.post(
            f"/api/v1/projects/{project.id}/gold",
            json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
        )
    ).json()["id"]
    resp = await client.delete(f"/api/v1/projects/{project.id}/gold/{gid}")
    assert resp.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project.id}/gold")).json()["items"] == []


async def test_delete_gold_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.delete(
        f"/api/v1/projects/{project.id}/gold/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


# --- Spec v4 R5 : les revues des autres participants sont visibles ------------------


async def test_from_reviews_blind_includes_colleagues(client, session, researcher_user, do_login):
    """R5: en mode aveugle, n'importe quel membre peut saisir la dernière revue d'un
    collègue (le filtre « revues de l'appelant » a disparu)."""
    from app.db.models import UserRole
    from tests.conftest import _make_user

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    colleague = await _make_user(
        session, "colleague@lab.fr", UserRole.RESEARCHER, "colleague-pass-123", True
    )
    project, article = await _make_project_with_article(session, researcher_user)
    session.add(
        ProjectMember(
            project_id=project.id, user_id=colleague.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    second = Article(project_id=project.id, pmid="222", title="Second")
    session.add(second)
    await session.commit()
    s = (
        await client.post(
            f"/api/v1/projects/{project.id}/searches",
            json={"kind": "blind", "selection": {"sample_n": 10, "seed": 1}},
        )
    ).json()
    r = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        params={"search": s["id"]},
        json=VALID_GT,
    )
    assert r.status_code == 201, r.text
    await do_login("colleague@lab.fr", "colleague-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/articles/{second.id}/annotation",
        params={"search": s["id"]},
        json={**VALID_GT, "use_ai_behavior": False},
    )
    assert r.status_code == 201, r.text
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold/from-reviews", json={}, params={"search": s["id"]}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["added"] == 2  # la revue du collègue est saisie aussi


async def test_gold_list_exposes_member_reviews(client, session, researcher_user, do_login):
    """R5: le listing gold expose, pour chaque référence, la dernière revue humaine par
    membre (nom + réponses aux questions + confiance)."""
    from app.db.models import UserRole
    from tests.conftest import _make_user

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    colleague = await _make_user(
        session, "colleague2@lab.fr", UserRole.RESEARCHER, "colleague-pass-123", True
    )
    project, article = await _make_project_with_article(
        session, researcher_user, fields=LEGACY_FIELDS
    )
    session.add(
        ProjectMember(
            project_id=project.id, user_id=colleague.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    await session.commit()
    r = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    assert r.status_code == 201, r.text
    # les deux membres relisent le même article
    r = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=VALID_GT
    )
    assert r.status_code == 201, r.text
    await do_login("colleague2@lab.fr", "colleague-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json={**VALID_GT, "use_ai_behavior": False},
    )
    assert r.status_code == 201, r.text
    items = (await client.get(f"/api/v1/projects/{project.id}/gold")).json()["items"]
    assert len(items) == 1
    revs = items[0]["human_reviews"]
    assert {r["user_id"] for r in revs} == {str(researcher_user.id), str(colleague.id)}
    by_uid = {r["user_id"]: r for r in revs}
    assert by_uid[str(researcher_user.id)]["answers"]["use_ai_behavior"] is True
    assert by_uid[str(colleague.id)]["answers"]["use_ai_behavior"] is False
    assert all(r["by"] and "confidence" in r and "at" in r for r in revs)
