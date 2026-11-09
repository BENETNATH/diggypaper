"""Whole-lot statistics (spec 2026-11-09-sous-analyse-criteres R5.2).

Deterministic compute over ``article_predictions`` (no LLM): per-field value
distribution + mean confidence, status distribution, N total. LATEST prediction
per article (house pattern). Empty fields absent. The JSON is injected verbatim
into the results-mode chat context.
"""

from datetime import UTC, datetime

from app.db.models import (
    Article,
    ArticlePrediction,
    PredictionStatus,
    Project,
    Search,
    SearchArticle,
)
from app.result_stats import compute_result_stats


async def _setup(session, n=4):
    project = Project(title="Stats P")
    session.add(project)
    await session.commit()
    search = Search(
        project_id=project.id,
        name="A",
        question="Q ?",
        fields=[
            {"key": "rodent", "label": "Modèle rongeur ?", "type": "boolean"},
            {"key": "duration", "label": "Durée (jours)", "type": "text"},
            {"key": "sex", "label": "Sexe", "type": "choice", "options": ["M", "F"]},
        ],
        kind="standard",
        lot_all=False,
    )
    session.add(search)
    await session.flush()
    decisions = [
        {"rodent": True, "duration": "120 j"},
        {"rodent": False, "duration": "30 j"},
        {"rodent": True},
        {"error": "invalid_json"},  # ERROR: counted in statuses, no field values
    ]
    confs = [0.9, 0.2, 0.5, None]
    fcs = [
        {"rodent": 0.92, "duration": 0.88},
        {"rodent": 0.81},
        None,  # legacy row: per-field confidence absent
        None,
    ]
    statuses = [
        PredictionStatus.AUTO_ACCEPTED,
        PredictionStatus.AUTO_REJECTED,
        PredictionStatus.NEEDS_HUMAN_REVIEW,
        PredictionStatus.ERROR,
    ]
    articles = []
    for i in range(n):
        a = Article(project_id=project.id, title=f"A{i}", pmid=str(2000 + i))
        session.add(a)
        await session.flush()  # a.id is assigned at flush time
        articles.append(a)
        session.add(SearchArticle(search_id=search.id, article_id=a.id))
        session.add(
            ArticlePrediction(
                article_id=a.id,
                search_id=search.id,
                model_used="m",
                decision_json=decisions[i],
                confidence_score=confs[i],
                field_confidences=fcs[i],
                status=statuses[i],
            )
        )
    # An article outside the lot: never counted.
    outside = Article(project_id=project.id, title="OUT", pmid="8888")
    session.add(outside)
    await session.flush()
    session.add(
        ArticlePrediction(
            article_id=outside.id,
            search_id=search.id,
            model_used="m",
            decision_json={"rodent": True},
            confidence_score=0.99,
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    await session.commit()
    return project, search, articles


async def test_exact_distributions(session):
    project, search, _ = await _setup(session)
    stats = await compute_result_stats(session, project.id, search.id)
    assert stats["n_results"] == 4
    assert stats["status"] == {
        "AUTO_ACCEPTED": 1,
        "AUTO_REJECTED": 1,
        "NEEDS_HUMAN_REVIEW": 1,
        "ERROR": 1,
    }
    # rodent: 2 true, 1 false (a3 ERROR has no usable decision)
    assert stats["fields"]["rodent"]["values"] == {"true": 2, "false": 1}
    # duration: a2 has none → 1 + 1
    assert stats["fields"]["duration"]["values"] == {"120 j": 1, "30 j": 1}
    # Empty field (no value anywhere) → absent.
    assert "sex" not in stats["fields"]


async def test_confidence_mean(session):
    project, search, _ = await _setup(session)
    stats = await compute_result_stats(session, project.id, search.id)
    # rodent: per-field confidences 0.92 (a0) + 0.81 (a1); a2 has no per-field
    # value → falls back to its global confidence 0.5.
    assert stats["fields"]["rodent"]["confidence_mean"] == round((0.92 + 0.81 + 0.5) / 3, 4)
    # duration: a0 0.88 (per-field); a1 global 0.2 (no per-field value).
    assert stats["fields"]["duration"]["confidence_mean"] == round((0.88 + 0.2) / 2, 4)


async def test_latest_prediction_wins(session):
    project, search, articles = await _setup(session)
    session.add(
        ArticlePrediction(
            article_id=articles[0].id,
            search_id=search.id,
            model_used="m2",
            decision_json={"rodent": False},
            confidence_score=0.3,
            field_confidences={"rodent": 0.3},
            status=PredictionStatus.AUTO_REJECTED,
            # SQLite func.now() is second-resolution: make "later" explicit.
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    stats = await compute_result_stats(session, project.id, search.id)
    # a0 now false: rodent 1 true / 2 false; status auto-accepted gone.
    assert stats["fields"]["rodent"]["values"] == {"true": 1, "false": 2}
    assert stats["status"]["AUTO_ACCEPTED"] == 0
    assert stats["status"]["AUTO_REJECTED"] == 2


async def test_no_predictions_empty_stats(session):
    project = Project(title="Empty")
    session.add(project)
    await session.commit()
    search = Search(
        project_id=project.id,
        name="A",
        lot_all=False,
        fields=[{"key": "rodent", "label": "R", "type": "boolean"}],
    )
    session.add(search)
    await session.commit()
    stats = await compute_result_stats(session, project.id, search.id)
    assert stats == {"n_results": 0, "status": {}, "fields": {}}


async def test_foreign_project_404(session):
    import pytest
    from fastapi import HTTPException

    project, search, _ = await _setup(session)
    other = Project(title="Other")
    session.add(other)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await compute_result_stats(session, other.id, search.id)
    assert exc.value.status_code == 404
