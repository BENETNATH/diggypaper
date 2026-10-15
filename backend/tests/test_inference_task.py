"""Tests de la task inférence massive (app/tasks/inference.py) — 100 % offline."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
)
from app.tasks.inference import _run_inference, _triage


class FakeInference:
    """Inference mockée : renvoie une prédiction fixe (confidence configurable)."""

    def __init__(self, confidence: float = 0.9):
        self._confidence = confidence
        self.calls = 0

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.calls += 1
        return {
            "use_ai_behavior": True,
            "animal_sex": "BOTH_SEXES",
            "confidence_score": self._confidence,
            "evidence_quotes": ["mice were randomized"],
            "reasoning_summary": "ok",
        }


# --- _triage ---


def test_triage_high_confidence_auto_accepted():
    assert _triage(0.9) == PredictionStatus.AUTO_ACCEPTED
    assert _triage(0.86) == PredictionStatus.AUTO_ACCEPTED


def test_triage_low_confidence_auto_rejected():
    assert _triage(0.1) == PredictionStatus.AUTO_REJECTED
    assert _triage(0.14) == PredictionStatus.AUTO_REJECTED


def test_triage_mid_confidence_needs_review():
    assert _triage(0.5) == PredictionStatus.NEEDS_HUMAN_REVIEW
    assert _triage(0.85) == PredictionStatus.NEEDS_HUMAN_REVIEW  # bornes incluses
    assert _triage(0.15) == PredictionStatus.NEEDS_HUMAN_REVIEW


def test_triage_none_needs_review():
    assert _triage(None) == PredictionStatus.NEEDS_HUMAN_REVIEW


# --- _run_inference ---


async def _seed(session: AsyncSession, project, prompt, model, n_articles: int = 2):
    for i in range(n_articles):
        article = Article(
            project_id=project.id, pmid=str(100 + i), title=f"A{i}", methods_text=f"Methods {i}."
        )
        session.add(article)
    await session.commit()


async def test_run_inference_completes(engine, session):
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Article Text:\n{methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, n_articles=2)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference(confidence=0.9)
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)

    assert result["processed"] == 2
    assert result["auto_accepted"] == 2
    assert result["auto_rejected"] == 0
    assert result["needs_review"] == 0
    assert fake.calls == 2
    # job COMPLETED
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    # predictions créées
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(preds) == 2
    assert all(p.status == PredictionStatus.AUTO_ACCEPTED for p in preds)


async def test_run_inference_triage_mixed(engine, session):
    """Confiances variées → triage mixte (AUTO_ACCEPTED / AUTO_REJECTED / NEEDS_HUMAN_REVIEW)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, n_articles=3)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    # 3 articles, confiances 0.9 / 0.1 / 0.5 → 1 accepted, 1 rejected, 1 review
    confidences = [0.9, 0.1, 0.5]

    class SeqInference:
        def __init__(self):
            self.i = 0

        async def infer(self, model, system_prompt, user_prompt) -> dict:
            c = confidences[self.i]
            self.i += 1
            return {
                "use_ai_behavior": True,
                "animal_sex": "BOTH_SEXES",
                "confidence_score": c,
                "evidence_quotes": [],
                "reasoning_summary": "ok",
            }

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=SeqInference())
    assert result["auto_accepted"] == 1
    assert result["auto_rejected"] == 1
    assert result["needs_review"] == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_run_inference_empty_corpus(engine, session):
    """Corpus vide → job COMPLETED, 0 processed."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=FakeInference())
    assert result["processed"] == 0
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_run_inference_failure_marks_failed(engine, session):
    """Échec inférence → job FAILED (pas bloqué en RUNNING)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, n_articles=1)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class FailInference:
        async def infer(self, model, system_prompt, user_prompt) -> dict:
            raise RuntimeError("LLM down")

    with pytest.raises(RuntimeError):
        await _run_inference(str(job.id), session_factory=factory, inference=FailInference())
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
