"""Cascade 2 étages (encodeur ONNX → modèle de chat) + driver ONNX — offline."""

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
from app.inference import InferenceError, onnx_driver, scoring
from app.tasks.inference import _run_inference


class FakeChat:
    def __init__(self):
        self.models: list[str] = []

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.models.append(model)
        return {"confidence_score": 0.9, "evidence_quotes": [], "reasoning_summary": ""}


async def test_prefilter_rejects_before_llm(engine, session, monkeypatch):
    scores = {"low": 0.05, "mid": 0.6}

    async def fake_score(identifier, text):
        assert identifier == "models/laya.onnx"
        return scores[text.split("\n")[0]]

    monkeypatch.setattr(scoring, "score_relevance", fake_score)
    project = Project(title="P", status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")
    session.add_all(
        [
            prompt,
            ModelRegistry(
                name="Laya",
                backend_type=BackendType.ONNX_LOCAL,
                model_identifier="models/laya.onnx",
            ),
            ModelRegistry(name="Kev", model_identifier="kev:q4"),
            Article(project_id=project.id, pmid="1", title="low", abstract="a"),
            Article(project_id=project.id, pmid="2", title="mid", abstract="a"),
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

    chat = FakeChat()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=chat)

    assert chat.models == ["kev:q4"]  # le LLM ne voit que l'article « mid », par identifiant
    assert (
        result["prefiltered"] == 1 and result["auto_rejected"] == 1 and result["auto_accepted"] == 1
    )
    preds = {
        p.model_used: p.status for p in (await session.execute(select(ArticlePrediction))).scalars()
    }
    assert preds == {"Laya": PredictionStatus.AUTO_REJECTED, "Kev": PredictionStatus.AUTO_ACCEPTED}


def test_onnx_score_sigmoid_and_softmax(monkeypatch):
    np = pytest.importorskip("numpy")

    class Enc:
        ids = [1, 2, 3]
        attention_mask = [1, 1, 1]

    class Tok:
        def encode(self, text):
            return Enc()

    class Sess:
        def __init__(self, out):
            self.out = out

        def run(self, _, feeds):
            assert set(feeds) == {"input_ids", "attention_mask"}
            return [np.array(self.out)]

    for name, out, expected in (
        ("a", [[0.0]], 0.5),
        ("b", [[0.0, 0.0]], 0.5),
        ("c", [[0, 20]], 1.0),
    ):
        monkeypatch.setitem(
            onnx_driver._SESSIONS, name, (Sess(out), Tok(), ["input_ids", "attention_mask"])
        )
        assert onnx_driver._score(name, "x") == pytest.approx(expected, abs=1e-3)
    onnx_driver.unload()


def test_onnx_missing_file_is_clean_error(monkeypatch):
    pytest.importorskip("onnxruntime")
    with pytest.raises(InferenceError, match="model_file_missing"):
        onnx_driver._load("nope/absent.onnx")


async def test_recovery_marks_running_jobs_failed(engine, session):
    from app import celery_app as ca

    project = Project(title="P", status=ProjectStatus.PROCESSING)
    session.add(project)
    await session.commit()
    run = InferenceJob(project_id=project.id, model_used="m", status=InferenceJobStatus.RUNNING)
    queued = InferenceJob(project_id=project.id, model_used="m", status=InferenceJobStatus.PENDING)
    session.add_all([run, queued])
    await session.commit()
    assert await ca._mark_interrupted(engine) >= 1
    await session.refresh(run)
    await session.refresh(queued)
    await session.refresh(project)
    assert run.status == InferenceJobStatus.FAILED and "redémarrage" in run.error
    assert queued.status == InferenceJobStatus.PENDING  # reste en file
    assert project.status == ProjectStatus.DRAFT
