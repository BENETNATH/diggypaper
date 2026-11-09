"""Filter by criteria over a search's results (spec 2026-11-09-sous-analyse-criteres §1).

Core: ``count_by_criteria`` / ``article_ids_by_criteria`` — AND over 3 families
(response / confidence / status) on the LATEST prediction per article of the
source search's lot. Empty criteria = the whole lot. Unknown field = 0 (not an
error). Blind search: criteria unavailable (house guard, oracle on masked
answers). Endpoints: GET criteria-count (query ``criteria`` = JSON), POST
criteria-articles (the effective lot, used when creating the derived search).
"""

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.db.models import (
    Article,
    ArticlePrediction,
    PredictionStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    SearchArticle,
)
from app.result_filter import article_ids_by_criteria, count_by_criteria

# Article i: decision / confidence / status (latest prediction of the search)
_DECISIONS = [
    {"rodent": True, "duration": "120 j"},
    {"rodent": True, "duration": "30 j"},
    {"rodent": False, "duration": "120 j"},
    {"rodent": False, "duration": "45 j"},
    {"rodent": True},  # duration absent
    {"rodent": False, "duration": "120 j"},
]
_CONFS = [0.9, 0.5, 0.1, 0.85, None, 0.5]
_STATUSES = [
    PredictionStatus.AUTO_ACCEPTED,
    PredictionStatus.NEEDS_HUMAN_REVIEW,
    PredictionStatus.AUTO_REJECTED,
    PredictionStatus.AUTO_ACCEPTED,
    PredictionStatus.ERROR,
    PredictionStatus.NEEDS_HUMAN_REVIEW,
]


async def _setup(session, n=6):
    """Project + standard search A (frozen lot of n articles, one prediction each)."""
    project = Project(title="Filter P")
    session.add(project)
    await session.commit()
    search = Search(
        project_id=project.id,
        name="A",
        question="Q ?",
        fields=[
            {"key": "rodent", "label": "Modèle rongeur ?", "type": "boolean"},
            {"key": "duration", "label": "Durée (jours)", "type": "text"},
        ],
        kind="standard",
        lot_all=False,
    )
    session.add(search)
    await session.flush()
    articles = []
    for i in range(n):
        a = Article(project_id=project.id, title=f"A{i}", pmid=str(1000 + i))
        session.add(a)
        await session.flush()  # a.id is assigned at flush time
        articles.append(a)
        session.add(SearchArticle(search_id=search.id, article_id=a.id))
        session.add(
            ArticlePrediction(
                article_id=a.id,
                search_id=search.id,
                model_used="m",
                decision_json=_DECISIONS[i],
                confidence_score=_CONFS[i],
                status=_STATUSES[i],
            )
        )
    # One article OUTSIDE the lot, with a matching prediction: never selected.
    outside = Article(project_id=project.id, title="OUT", pmid="9999")
    session.add(outside)
    await session.flush()
    session.add(
        ArticlePrediction(
            article_id=outside.id,
            search_id=search.id,
            model_used="m",
            decision_json={"rodent": True, "duration": "120 j"},
            confidence_score=0.99,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    return project, search, articles


# ---------------------------------------------------------------------------
# Core — app/result_filter
# ---------------------------------------------------------------------------


async def test_empty_criteria_is_the_whole_lot(session):
    project, search, articles = await _setup(session)
    assert await count_by_criteria(session, project.id, search.id, {}) == 6
    ids = await article_ids_by_criteria(session, project.id, search.id, {})
    assert set(ids) == {a.id for a in articles}


async def test_response_equal(session):
    project, search, _ = await _setup(session)
    crit = {"response": {"field": "rodent", "op": "=", "value": "true"}}
    assert await count_by_criteria(session, project.id, search.id, crit) == 3  # a0, a1, a4
    crit_false = {"response": {"field": "rodent", "op": "=", "value": "false"}}
    assert await count_by_criteria(session, project.id, search.id, crit_false) == 3


async def test_response_not_equal_excludes_missing_values(session):
    project, search, _ = await _setup(session)
    crit = {"response": {"field": "duration", "op": "!=", "value": "120 j"}}
    # a1 (30 j), a3 (45 j) — a4 has no duration (excluded), the 120 j ones too.
    assert await count_by_criteria(session, project.id, search.id, crit) == 2


async def test_response_contains(session):
    project, search, _ = await _setup(session)
    crit = {"response": {"field": "duration", "op": "contains", "value": "120"}}
    assert await count_by_criteria(session, project.id, search.id, crit) == 3  # a0, a2, a5


async def test_unknown_field_zero_results(session):
    project, search, _ = await _setup(session)
    crit = {"response": {"field": "nope", "op": "=", "value": "x"}}
    assert await count_by_criteria(session, project.id, search.id, crit) == 0
    assert await article_ids_by_criteria(session, project.id, search.id, crit) == []


async def test_confidence_range_bounds_inclusive(session):
    project, search, _ = await _setup(session)
    # confs: 0.9, 0.5, 0.1, 0.85, None, 0.5 — [0.5, 0.85] keeps 0.5, 0.85 and 0.5.
    assert await count_by_criteria(session, project.id, search.id, {"confidence": [0.5, 0.85]}) == 3
    assert await count_by_criteria(session, project.id, search.id, {"confidence": [None, 0.5]}) == 3
    assert await count_by_criteria(session, project.id, search.id, {"confidence": [0.9, None]}) == 1
    # A missing confidence never matches a bounded range.
    assert (
        await count_by_criteria(session, project.id, search.id, {"confidence": [None, None]}) == 6
    )


async def test_status_multi_select(session):
    project, search, _ = await _setup(session)
    crit = {"status": ["AUTO_ACCEPTED", "ERROR"]}
    assert await count_by_criteria(session, project.id, search.id, crit) == 3  # a0, a3, a4
    assert await count_by_criteria(session, project.id, search.id, {"status": ["ERROR"]}) == 1


async def test_multi_family_and(session):
    project, search, _ = await _setup(session)
    # rodent=true {a0, a1, a4} AND conf >= 0.8 {a0} AND status auto-accepted {a0, a3}
    crit = {
        "response": {"field": "rodent", "op": "=", "value": "true"},
        "confidence": [0.8, None],
        "status": ["AUTO_ACCEPTED"],
    }
    assert await count_by_criteria(session, project.id, search.id, crit) == 1
    ids = await article_ids_by_criteria(session, project.id, search.id, crit)
    assert len(ids) == 1


async def test_zero_results(session):
    project, search, _ = await _setup(session)
    crit = {"response": {"field": "duration", "op": "contains", "value": "999"}}
    assert await count_by_criteria(session, project.id, search.id, crit) == 0
    assert await article_ids_by_criteria(session, project.id, search.id, crit) == []


async def test_latest_prediction_wins_on_rerun(session):
    """Re-run: the LATEST prediction per article answers the criteria (house
    pattern of article_filter). The stale row of a re-run is ignored."""
    project, search, articles = await _setup(session)
    # Re-run: a0 flips to false, a4 gains a duration + higher confidence.
    session.add(
        ArticlePrediction(
            article_id=articles[0].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"rodent": False, "duration": "120 j"},
            confidence_score=0.95,
            status=PredictionStatus.AUTO_ACCEPTED,
            # SQLite func.now() is second-resolution: make "later" explicit.
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    session.add(
        ArticlePrediction(
            article_id=articles[4].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"rodent": True, "duration": "200 j"},
            confidence_score=0.95,
            status=PredictionStatus.AUTO_ACCEPTED,
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    # a0 is now false → rodent=true keeps a1, a4 only.
    crit = {"response": {"field": "rodent", "op": "=", "value": "true"}}
    assert await count_by_criteria(session, project.id, search.id, crit) == 2
    ids = await article_ids_by_criteria(session, project.id, search.id, crit)
    assert set(ids) == {articles[1].id, articles[4].id}


async def test_blind_search_rejected(session):
    project = Project(title="Blind P")
    session.add(project)
    await session.commit()
    search = Search(
        project_id=project.id,
        name="B",
        kind="blind",
        lot_all=False,
        fields=[{"key": "rodent", "label": "R", "type": "boolean"}],
    )
    session.add(search)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        crit_true = {"response": {"field": "rodent", "op": "=", "value": "true"}}
        await count_by_criteria(session, project.id, search.id, crit_true)
    assert exc.value.status_code == 422


async def test_foreign_project_search_404(session):
    project, search, _ = await _setup(session)
    other = Project(title="Other")
    session.add(other)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await count_by_criteria(session, other.id, search.id, {})
    assert exc.value.status_code == 404


async def test_invalid_criteria_422(session):
    project, search, _ = await _setup(session)
    bad = [
        {"status": ["AUTO_ACCEPTED", "AUTO_REJECTED", "NEEDS_HUMAN_REVIEW", "ERROR", "NONE"]},
        {"status": ["NOPE"]},
        {"confidence": [0, 2]},
        {"confidence": [0.5]},
        {"response": {"field": "rodent", "op": ">>", "value": "true"}},
        {"response": {"field": "rodent", "op": "=", "value": ""}},
    ]
    for crit in bad:
        with pytest.raises(HTTPException) as exc:
            await count_by_criteria(session, project.id, search.id, crit)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _login_session(session, researcher_user) -> str:
    """A second project the researcher LEADS, with the same fixture search."""
    project, search, _ = await _setup(session)
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    return str(project.id), str(search.id)


async def test_criteria_count_endpoint(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, search_id = await _login_session(session, researcher_user)
    resp = await client.get(
        f"/api/v1/projects/{project_id}/searches/{search_id}/criteria-count",
        params={"criteria": json.dumps({"status": ["AUTO_ACCEPTED"]})},
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}
    # Empty / absent criteria = the whole lot.
    resp = await client.get(f"/api/v1/projects/{project_id}/searches/{search_id}/criteria-count")
    assert resp.json() == {"count": 6}


async def test_criteria_articles_endpoint(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, search_id = await _login_session(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/searches/{search_id}/criteria-articles",
        json={"response": {"field": "rodent", "op": "=", "value": "true"}},
    )
    assert resp.status_code == 200
    ids = resp.json()["article_ids"]
    assert len(ids) == 3
    assert all(isinstance(i, str) and len(i) == 36 for i in ids)


async def test_criteria_endpoints_foreign_search_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id, _ = await _login_session(session, researcher_user)
    other = Project(title="Foreign")
    session.add(other)
    await session.flush()
    foreign = Search(project_id=other.id, name="F", lot_all=True)
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project_id}/searches/{foreign.id}/criteria-count")
    assert resp.status_code == 404
    resp = await client.post(
        f"/api/v1/projects/{project_id}/searches/{foreign.id}/criteria-articles", json={}
    )
    assert resp.status_code == 404
