"""Question + project-specific fields (app/schema.py) — offline."""

import pytest

from app.schema import build_system_prompt, check_fields, compute_metrics, validate_decision
from app.searches import ensure_default_search
from tests.test_gold_api import _make_project_with_article

FIELDS = [
    {"key": "design", "label": "Design", "type": "choice", "options": ["RCT", "COHORT"]},
    {"key": "blinded", "label": "Blinded", "type": "boolean"},
]


def test_check_fields_rejects_bad_input():
    assert check_fields(FIELDS) == FIELDS
    for bad in (
        [],
        [{"key": "Bad Key", "type": "text"}],
        [{"key": "confidence_score", "type": "text"}],
        [{"key": "watch_q_0", "type": "boolean"}],
        [{"key": "a", "type": "choice", "options": ["x"]}],
        [{"key": "a", "type": "text"}, {"key": "a", "type": "text"}],
    ):
        with pytest.raises(ValueError):
            check_fields(bad)


def test_build_system_prompt_requires_question_or_fields():
    """spec 2026-11-06-ux-refonte-v4 R1: nothing to answer at all → refused."""
    with pytest.raises(ValueError, match="question or fields required"):
        build_system_prompt(None, [])
    with pytest.raises(ValueError, match="question or fields required"):
        build_system_prompt("   ", [])


def test_build_system_prompt_neutral_without_question():
    """spec 2026-11-06-ux-refonte-v4 R1: an empty orientation is fine — the
    prompt is built from the answer items alone (no question line)."""
    prompt = build_system_prompt(None, FIELDS)
    assert "answer this question" not in prompt
    assert '"design": "RCT" | "COHORT"' in prompt
    assert build_system_prompt("   ", FIELDS) == prompt  # blank == absent


def test_prompt_evidence_quotes_per_field():
    """spec 2026-11-23 P-5: the model answers evidence_quotes as a dict, one
    list per field (up to 3 verbatim quotes, nothing for an unsupported field)."""
    prompt = build_system_prompt("Blinding?", FIELDS)
    assert '"evidence_quotes": { "<field key>": [ string' in prompt
    assert "up to 3" in prompt
    assert "or nothing" in prompt


def test_compute_metrics_flattens_dict_evidence_quotes():
    """spec 2026-11-23 P-5: dict (per field) and flat list (legacy) give the
    same P/R/F1 — no migration of existing predictions."""
    truth = {"design": "RCT", "blinded": True, "evidence_quotes": ["q1"]}
    flat = {"design": "RCT", "blinded": True, "evidence_quotes": ["q1", "q2"]}
    m_flat = compute_metrics(FIELDS, flat, truth)
    m_dict = compute_metrics(
        FIELDS,
        {
            "design": "RCT",
            "blinded": True,
            "evidence_quotes": {"design": ["q1", "q2"], "blinded": []},
        },
        truth,
    )
    assert m_dict == m_flat


def test_validate_metrics_prompt():
    ok = {"design": "RCT", "blinded": True, "confidence_score": 0.5}
    assert validate_decision(FIELDS, ok) == []
    assert validate_decision(FIELDS, {"design": "X", "blinded": "yes"})
    m = compute_metrics(FIELDS, ok, {"design": "rct", "blinded": False})
    assert m["accuracy"] == 0.5
    prompt = build_system_prompt("Is it blinded?", FIELDS)
    assert "Is it blinded?" in prompt and '"design": "RCT" | "COHORT"' in prompt


async def test_custom_schema_flow(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    await ensure_default_search(session, project)
    base = f"/api/v1/projects/{project.id}"
    r = await client.patch(base, json={"question": "Blinding?", "fields": FIELDS})
    assert r.status_code == 200 and r.json()["custom_schema"] is True
    assert (
        await client.patch(base, json={"fields": [{"key": "x", "type": "nope"}]})
    ).status_code == 422
    gt = {"design": "RCT", "blinded": True}
    r = await client.post(
        f"{base}/gold", json={"article_id": str(article.id), "ground_truth_json": gt}
    )
    assert r.status_code == 201
    r = await client.post(
        f"{base}/gold", json={"article_id": str(article.id), "ground_truth_json": {"design": "?"}}
    )
    assert r.status_code == 422
    r = await client.post(f"{base}/articles/{article.id}/annotation", json=gt)
    assert r.status_code == 201 and r.json()["decision_json"]["design"] == "RCT"
    r = await client.post(f"{base}/prompts/generate")
    assert r.status_code == 201
    assert "Blinding?" in r.json()["system_prompt"] and r.json()["version_number"] == "v1.0"


async def test_articles_prediction_status_and_auto_prompt(
    client, session, researcher_user, do_login
):
    from app.db.models import ArticlePrediction, PredictionStatus

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    search = await ensure_default_search(session, project)
    search.question = "Which design?"  # a prompt needs a real question (no legacy fallback)
    await session.commit()
    base = f"/api/v1/projects/{project.id}"
    r = (await client.get(f"{base}/articles")).json()
    assert r["items"][0]["prediction_status"] is None and r["stats"] == {"NONE": 1, "TO_REVIEW": 0}
    session.add(
        ArticlePrediction(
            article_id=article.id,
            model_used="m",
            confidence_score=0.5,
            status=PredictionStatus.NEEDS_HUMAN_REVIEW,
        )
    )
    await session.commit()
    r = (await client.get(f"{base}/articles", params={"status": "TO_REVIEW"})).json()
    assert r["total"] == 1 and r["items"][0]["confidence"] == 0.5
    assert (await client.get(f"{base}/articles", params={"status": "NONE"})).json()["total"] == 0
    # the auto prompt is reused as long as the question does not change
    p1 = (await client.post(f"{base}/prompts/generate")).json()
    assert (await client.post(f"{base}/prompts/generate")).json()["id"] == p1["id"]
