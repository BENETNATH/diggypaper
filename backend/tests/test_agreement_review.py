"""Tests for reviewer agreement metrics (spec 2026-11-07-accord-reviews).

Purely calculational: agreement between two human reviewers (acc + Cohen kappa,
per field and global on the main field), between the AI and a human reviewer,
the disagreement list, the endpoint payload, permissions, and the PRISMA
summary text.

Reference kappa values are computed by hand (2x2 tables), not tautological.
"""

import uuid

import pytest

from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    User,
)

FIELDS = [
    {"key": "include", "type": "boolean"},
    {"key": "study_type", "type": "choice", "options": ["RCT", "COHORT"]},
]


async def _make_user(session, email, password="rev-pass-123") -> User:
    from app.core.security import hash_password
    from app.db.models import UserRole

    u = User(
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
        can_create_projects=True,
    )
    session.add(u)
    await session.commit()
    return u


async def _seed(session, lead, fields=FIELDS, n_articles=4):
    """Project + search + 4 articles; returns (project, search, [articles])."""
    project = Project(title="P", created_by=lead.id, fields=fields, question="Q")
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=lead.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    search = Search(project_id=project.id, name="S", question="Q", fields=fields)
    session.add(search)
    await session.commit()
    arts = []
    for i in range(n_articles):
        a = Article(project_id=project.id, pmid=str(1000 + i), title=f"T{i}")
        session.add(a)
        arts.append(a)
    await session.commit()
    return project, search, arts


def _ann(article, user, decision, search, when=None) -> HumanAnnotation:
    return HumanAnnotation(
        article_id=article.id,
        user_id=user.id,
        search_id=search.id,
        decision_json=decision,
        created_at=when,
    )


async def _annotate(session, search, arts, user_a, user_b, dec_a, dec_b):
    """dec_a/dec_b: lists of decisions (one per article)."""
    session.add(_ann(arts[0], user_a, dec_a[0], search))
    session.add(_ann(arts[1], user_a, dec_a[1], search))
    session.add(_ann(arts[2], user_a, dec_a[2], search))
    session.add(_ann(arts[3], user_a, dec_a[3], search))
    session.add(_ann(arts[0], user_b, dec_b[0], search))
    session.add(_ann(arts[1], user_b, dec_b[1], search))
    session.add(_ann(arts[2], user_b, dec_b[2], search))
    session.add(_ann(arts[3], user_b, dec_b[3], search))
    await session.commit()


# --- §unit: cohen_kappa (hand-computed references) ---


def test_kappa_classic_half():
    """A=[T,T,F,F] B=[T,T,F,T]: po=0.75, pe=(2/4)(3/4)+(2/4)(1/4)=0.5, kappa=0.5."""
    from app.agreement import cohen_kappa

    pairs = [(True, True), (True, True), (False, False), (False, True)]
    assert cohen_kappa(pairs) == pytest.approx(0.5)


def test_kappa_chance_only():
    """A=[T,T,F,F] B=[T,F,T,F]: po=0.5, pe=0.5, kappa=0.0."""
    from app.agreement import cohen_kappa

    pairs = [(True, True), (True, False), (False, True), (False, False)]
    assert cohen_kappa(pairs) == pytest.approx(0.0)


def test_kappa_empty_and_unary():
    from app.agreement import cohen_kappa

    assert cohen_kappa([]) is None
    assert cohen_kappa([(True, True)]) is None  # pe == 1: undefined


def test_kappa_multimodal_returns_none():
    from app.agreement import cohen_kappa

    pairs = [("RCT", "RCT"), ("COHORT", "COHORT"), ("CASE", "CASE")]
    assert cohen_kappa(pairs) is None  # > 2 modalities: raw agreement only


# --- §1: agreement human-human (endpoint) ---

DEC_A = [
    {"include": True, "study_type": "RCT"},
    {"include": "oui", "study_type": "COHORT"},  # normalization: "oui" == True
    {"include": False, "study_type": "RCT"},
    {"include": False, "study_type": "COHORT"},
]
# B differs from A on article 3 (include) and article 1 (study_type)
DEC_B = [
    {"include": True, "study_type": "COHORT"},
    {"include": True, "study_type": "RCT"},
    {"include": False, "study_type": "RCT"},
    {"include": True, "study_type": "COHORT"},
]


async def test_human_human_payload(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    h = body["humans"]
    # main field include: pairs (T,T)(T,T)(F,F)(F,T) -> po=0.75, pe=0.5, kappa=0.5
    assert h["n"] == 4
    assert h["n_agree"] == 3
    assert h["acc"] == pytest.approx(0.75)
    assert h["kappa"] == pytest.approx(0.5)
    by_field = {f["field"]: f for f in h["by_field"]}
    # study_type: (RCT,COHORT)(COHORT,RCT)(RCT,RCT)(COHORT,COHORT) -> acc=0.5, kappa=0.0
    assert by_field["study_type"]["n"] == 4
    assert by_field["study_type"]["n_agree"] == 2
    assert by_field["study_type"]["acc"] == pytest.approx(0.5)
    assert by_field["study_type"]["kappa"] == pytest.approx(0.0)
    assert h["disagreements"] or body["disagreements"]


async def test_disagreements_exactly_the_differing_articles(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id},
    )
    dis = resp.json()["disagreements"]
    # main field differs only on article 3 (index 3): A=False, B=True
    assert len(dis) == 1
    d = dis[0]
    assert d["article_id"] == str(arts[3].id)
    assert d["title"] == "T3"
    assert d["value_a"] is False
    assert d["value_b"] is True


async def test_empty_intersection(client, session, researcher_user, do_login):
    """Two reviewers annotating disjoint articles -> n == 0, kappa null, no error."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    session.add(_ann(arts[0], a, {"include": True}, search))
    session.add(_ann(arts[1], b, {"include": True}, search))
    await session.commit()

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id},
    )
    assert resp.status_code == 200
    h = resp.json()["humans"]
    assert h["n"] == 0
    assert h["kappa"] is None
    assert h["acc"] is None


async def test_single_reviewer_no_error(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    session.add(_ann(arts[0], a, {"include": True}, search))
    await session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/agreement")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_reviewers"] == 1
    assert body["humans"]["n"] == 0


# --- §2: agreement AI-human ---

DEC_AI = [
    {"include": True, "study_type": "RCT"},
    {"include": True, "study_type": "COHORT"},
    {"include": False, "study_type": "RCT"},
    {"include": False, "study_type": "COHORT"},
]


async def test_ai_human(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)
    for art, dec in zip(arts, DEC_AI, strict=True):
        session.add(
            ArticlePrediction(
                article_id=art.id,
                search_id=search.id,
                model_used="m1",
                decision_json=dec,
                confidence_score=0.9,
            )
        )
    await session.commit()

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id, "model": "m1"},
    )
    ai = resp.json()["ai"]
    assert ai is not None
    # AI vs B: (T,T)(T,T)(F,F)(F,T) -> acc 0.75, kappa 0.5, n 4
    assert ai["n"] == 4
    assert ai["n_agree"] == 3
    assert ai["acc"] == pytest.approx(0.75)
    assert ai["kappa"] == pytest.approx(0.5)
    assert ai["user"]["id"] == str(b.id)


async def test_ai_null_without_model(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id},
    )
    assert resp.json()["ai"] is None


async def test_ai_no_intersection_null(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)
    session.add(
        ArticlePrediction(
            article_id=arts[0].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"include": True},
            confidence_score=0.5,
        )
    )
    # prediction only by model m2; user A annotated arts 0-3 -> intersection exists for m2
    # but request model m9 (no predictions at all)
    await session.commit()
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id, "model": "m9"},
    )
    assert resp.json()["ai"] is None


# --- §4: defaults, errors, permissions ---


async def test_default_pair_most_active(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    c = await _make_user(session, "rev-c@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)
    session.add(_ann(arts[0], c, {"include": True}, search))
    await session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/agreement")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_reviewers"] == 3
    pair = {body["humans"]["user_a"]["id"], body["humans"]["user_b"]["id"]}
    assert pair == {str(a.id), str(b.id)}


async def test_same_user_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": a.id},
    )
    assert resp.status_code == 422


async def test_unknown_user_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _seed(session, researcher_user)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": uuid.uuid4(), "user_b": uuid.uuid4()},
    )
    assert resp.status_code == 404


async def test_foreign_search_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _seed(session, researcher_user)
    other = Project(title="O", created_by=researcher_user.id, fields=FIELDS)
    session.add(other)
    await session.commit()
    other_search = Search(project_id=other.id, name="OS", fields=FIELDS)
    session.add(other_search)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{other_search.id}/agreement")
    assert resp.status_code == 404


async def test_non_member_403(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, _ = await _seed(session, researcher_user)
    outsider = await _make_user(session, "outsider@lab.fr")
    await do_login("outsider@lab.fr", "rev-pass-123")
    resp = await client.get(f"/api/v1/projects/{project.id}/searches/{search.id}/agreement")
    assert resp.status_code == 403
    assert outsider is not None


# --- §6: PRISMA summary text ---


async def test_summary_text_contains_numbers(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)
    for art, dec in zip(arts, DEC_AI, strict=True):
        session.add(
            ArticlePrediction(
                article_id=art.id,
                search_id=search.id,
                model_used="m1",
                decision_json=dec,
                confidence_score=0.9,
            )
        )
    await session.commit()

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id, "model": "m1"},
        headers={"accept-language": "fr"},
    )
    summary = resp.json()["summary"]
    assert "4" in summary  # n articles
    assert "75" in summary  # 75.0 %
    assert "0.5" in summary  # kappa
    assert "1" in summary  # 1 disagreement
    assert "m1" in summary  # model named


async def test_summary_english(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, search, arts = await _seed(session, researcher_user)
    a = await _make_user(session, "rev-a@lab.fr")
    b = await _make_user(session, "rev-b@lab.fr")
    await _annotate(session, search, arts, a, b, DEC_A, DEC_B)

    resp = await client.get(
        f"/api/v1/projects/{project.id}/searches/{search.id}/agreement",
        params={"user_a": a.id, "user_b": b.id},
        headers={"accept-language": "en"},
    )
    summary = resp.json()["summary"]
    assert "articles" in summary.lower() or "reviewed" in summary.lower()
