"""Gold Dataset route tests (app/api/v1/gold.py) — 100% offline."""

from app.db.models import Article, HumanAnnotation, Project, ProjectMember, ProjectMemberRole

VALID_GT = {
    "use_ai_behavior": True,
    "animal_sex": "BOTH_SEXES",
    "confidence_score": 0.9,
    "evidence_quotes": ["mice were randomized"],
    "reasoning_summary": "ok",
}


async def _make_project_with_article(session, lead_user) -> tuple[Project, Article]:
    project = Project(title="P", created_by=lead_user.id)
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
    """Un identifiant d'article d'un autre projet (portant une relecture « legacy »,
    ``search_id`` NULL) ne doit jamais créer de référence dans le projet appelant."""
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
