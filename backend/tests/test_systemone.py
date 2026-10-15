"""Pilote des modèles de décision (/v1/systemone) + cascade avec scoreurs — offline."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
)
from app.inference import InferenceError
from app.inference.systemone import (
    WINDOW_CHARS,
    build_questions,
    decide,
    parse_answers,
    split_identifier,
    split_windows,
)
from app.schema import DEFAULT_FIELDS
from app.tasks.inference import _run_inference

FIELDS = DEFAULT_FIELDS + [{"key": "notes", "label": "Notes", "type": "text"}]


def test_split_and_questions():
    assert split_identifier("http://laya:8000/#multilingual") == (
        "http://laya:8000",
        "multilingual",
    )
    assert split_identifier("http://kev:8080") == ("http://kev:8080", None)
    qs = build_questions("Is it relevant?", FIELDS)
    assert qs["relevance"] == {"type": "noul", "instructions": "Is it relevant?"}
    assert qs["use_ai_behavior"]["type"] == "noul" and qs["animal_sex"]["type"] == "choice"
    assert "notes" not in qs  # texte libre non supporté


def test_parse_answers():
    result = {
        "model": "m",
        "answers": {
            "relevance": {"type": "noul", "noul": 0.93},
            "use_ai_behavior": {"type": "noul", "noul": 0.2},
            "animal_sex": {"type": "choice", "choice": "BOTH_SEXES", "probabilities": {}},
        },
    }
    out = parse_answers(result, FIELDS)
    assert out["confidence_score"] == 0.93 and out["decision"] == "INCLUDED"
    assert out["use_ai_behavior"] is False and out["animal_sex"] == "BOTH_SEXES"
    with pytest.raises(InferenceError, match="invalid_response"):
        parse_answers({"answers": {}}, FIELDS)


async def test_decide_http_and_errors():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), req.read()
        if b"boom" in seen["body"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"answers": {"relevance": {"type": "noul", "noul": 0.5}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await decide("http://s:1#english", "text", "Q?", [], http=http)
        assert out["confidence_score"] == 0.5
        assert seen["url"] == "http://s:1/v1/systemone" and b'"model":"english"' in seen["body"]
        with pytest.raises(InferenceError, match="HTTP 500"):
            await decide("http://s:1", "boom", "Q?", [], http=http)


async def test_cascade_with_decision_models(engine, session, monkeypatch):
    calls = []

    async def fake_decide(identifier, text, question, fields, **kw):
        calls.append((identifier, text.split("\n")[0]))
        p = {"low": 0.05, "mid": 0.5, "high": 0.9}[text.split("\n")[0]]
        return {"confidence_score": p, "evidence_quotes": [], "decision": "X"}

    from app.inference import scoring

    monkeypatch.setattr(scoring, "decide", fake_decide)
    project = Project(title="P", status=ProjectStatus.FROZEN, question="Q?")
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")
    session.add_all(
        [
            prompt,
            ModelRegistry(
                name="Laya", backend_type=BackendType.SYSTEMONE, model_identifier="http://l#ml"
            ),
            ModelRegistry(
                name="Kev", backend_type=BackendType.SYSTEMONE, model_identifier="http://k"
            ),
            Article(project_id=project.id, pmid="1", title="low", abstract="low"),
            Article(project_id=project.id, pmid="2", title="high", abstract="high"),
        ]
    )
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev",
        prefilter_model="Laya",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=object())
    # étage 1 sur les 2 articles ; étage 2 (Kev) seulement sur « high »
    assert [c[0] for c in calls] == ["http://l#ml", "http://l#ml", "http://k"]
    assert (
        result["prefiltered"] == 1 and result["auto_rejected"] == 1 and result["auto_accepted"] == 1
    )
    preds = {
        p.model_used: p.status for p in (await session.execute(select(ArticlePrediction))).scalars()
    }
    assert preds == {
        "Laya": PredictionStatus.AUTO_REJECTED,
        "Kev": PredictionStatus.AUTO_ACCEPTED,
    }


def test_split_windows():
    assert split_windows("short", 4) == ["short"]
    long_ = "x" * (WINDOW_CHARS * 5)
    assert len(split_windows(long_, 3)) == 3 and len(split_windows(long_, 99)) > 3
    assert all(len(w) <= WINDOW_CHARS for w in split_windows(long_, 99))


async def test_decide_windows_best_window_then_details():
    import json

    scores = {0: 0.2, 1: 0.9, 2: 0.4}
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        b = json.loads(req.read())
        calls.append(sorted(b["questions"]))
        if len(b["questions"]) == 1:  # passes de pertinence, une par fenêtre
            p = scores[len(calls) - 1]
            return httpx.Response(200, json={"answers": {"relevance": {"type": "noul", "noul": p}}})
        return httpx.Response(
            200,
            json={
                "answers": {
                    "relevance": {"type": "noul", "noul": 0.9},
                    "animal_sex": {"type": "choice", "choice": "BOTH_SEXES"},
                }
            },
        )

    fields = [DEFAULT_FIELDS[1]]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await decide(
            "http://s:1", "y" * (WINDOW_CHARS * 3), "Q?", fields, http=http, windows=3
        )
    assert (
        out["confidence_score"] == 0.9 and out["animal_sex"] == "BOTH_SEXES" and out["windows"] == 3
    )
    assert [len(c) for c in calls] == [
        1,
        1,
        1,
        2,
    ]  # 3 fenêtres (1 question) + détails (2 questions)


def test_parse_answers_certainty_per_field():
    fields = [
        {"key": "ai", "label": "AI", "type": "boolean"},
        {"key": "sex", "label": "Sex", "type": "choice", "options": ["A", "B"]},
    ]
    out = parse_answers(
        {
            "answers": {
                "relevance": {"noul": 0.9},
                "ai": {"noul": 0.2},
                "sex": {"choice": "B", "probabilities": {"A": 0.3, "B": 0.7}},
            }
        },
        fields,
    )
    assert out["ai"] is False and out["field_confidence"]["ai"] == 0.8
    assert out["sex"] == "B" and out["field_confidence"]["sex"] == 0.7
    out2 = parse_answers(
        {"answers": {"relevance": {"noul": 0.9}, "ai": {"noul": 0.2, "answer_confidence": 0.55}}},
        fields[:1],
    )
    assert out2["field_confidence"]["ai"] == 0.55
