"""Tests for the mass inference task (app/tasks/inference.py) — 100% offline."""

import asyncio
import json
import uuid

import httpx
import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
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
from app.inference import InferenceError
from app.settings.service import set_setting
from app.tasks import inference as inference_task_module
from app.tasks.inference import ModelBusy, _release_lock, _run_inference, _triage, inference_task


class FakeInference:
    """Mocked inference: returns a fixed prediction (confidence configurable)."""

    def __init__(self, confidence: float = 0.9):
        self._confidence = confidence
        self.calls = 0
        self.warmups = 0

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.calls += 1
        if '{"ok": true}' in user_prompt:
            self.warmups += 1
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


async def test_run_inference_completes(engine, session, fake_redis):
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
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=fake, redis=fake_redis
    )

    assert result["processed"] == 2
    assert result["auto_accepted"] == 2
    assert result["auto_rejected"] == 0
    assert result["needs_review"] == 0
    # 1 warm-up (cold start) + 2 articles
    assert fake.warmups == 1 and fake.calls == 3
    # job COMPLETED
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    # predictions created
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(preds) == 2
    assert all(p.status == PredictionStatus.AUTO_ACCEPTED for p in preds)


async def test_run_inference_triage_mixed(engine, session, fake_redis):
    """Varied confidences -> mixed triage (AUTO_ACCEPTED / AUTO_REJECTED / NEEDS_HUMAN_REVIEW)."""
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
            if '{"ok": true}' in user_prompt:  # warm-up : ne consomme pas de confiance
                return {"ok": True}
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
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=SeqInference(), redis=fake_redis
    )
    assert result["auto_accepted"] == 1
    assert result["auto_rejected"] == 1
    assert result["needs_review"] == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_run_inference_empty_corpus(engine, session, fake_redis):
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
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 0
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_run_inference_failure_marks_failed(engine, session, fake_redis):
    """Inference failure -> job FAILED (not stuck in RUNNING)."""
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
        await _run_inference(
            str(job.id), session_factory=factory, inference=FailInference(), redis=fake_redis
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED


async def test_run_inference_stops_when_cancelled(engine, session, fake_redis):
    """Stop requested mid-way: stops before the next article, no email."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=4)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    class StopAfterFirst(FakeInference):
        async def infer(self, model, system_prompt, user_prompt) -> dict:
            out = await super().infer(model, system_prompt, user_prompt)
            if '{"ok": true}' in user_prompt:
                return out  # warm-up : pas d'arrêt simulé pendant celui-ci
            row = await session.get(InferenceJob, job.id)
            row.status, row.error = InferenceJobStatus.FAILED, "cancelled"  # clic sur « Arrêter »
            await session.commit()
            return out

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = StopAfterFirst()
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=fake, redis=fake_redis
    )
    # warm-up + 1 article processed, then stop
    assert result["cancelled"] is True and result["processed"] == 1 and fake.calls == 2
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED and job.error == "cancelled"
    assert len((await session.execute(select(ArticlePrediction))).scalars().all()) == 1


async def test_run_inference_skips_job_cancelled_before_start(engine, session):
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.FAILED,
        error="cancelled",
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference()
    assert await _run_inference(str(job.id), session_factory=factory, inference=fake) == {}
    assert fake.calls == 0


# --- Warm-up (cold start Ollama) ---


async def test_warmup_once_for_ollama_model(engine, session, fake_redis):
    """Local model: a single warm-up (absorbs the weights loading), not one per article."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=2)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference()
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=fake, redis=fake_redis
    )
    assert result["processed"] == 2
    assert fake.warmups == 1 and fake.calls == 3


async def test_warmup_skipped_for_openai_api_model(engine, session):
    """Remote backend (OPENAI_API): no local loading -> no warm-up."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(
        ModelRegistry(
            name="GPT",
            model_identifier="https://llm.test/v1#gpt-4o-mini",
            backend_type="OPENAI_API",
        )
    )
    await session.commit()
    # 0 articles: the loop never calls infer_with (stays 100% offline)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="GPT",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference()
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)
    assert result["processed"] == 0
    assert fake.warmups == 0 and fake.calls == 0


# --- Per-model lock (Redis) ---


async def test_lock_key_ttl_and_token(engine, session, fake_redis):
    """Key inference:lock:<backend>:<identifier>, SET NX, TTL = timeout + 300 s, hex token."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="qwen3:4b", model_identifier="qwen3:4b", backend_type="OLLAMA"))
    await session.commit()
    await set_setting(session, "inference_timeout", "600")
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="qwen3:4b",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_inference(
        str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
    )
    key, token, nx, ex = fake_redis.set_calls[0]
    assert key == "inference:lock:OLLAMA:qwen3:4b"
    assert nx is True
    assert ex == 900  # 600 s (inference_timeout) + 300 s
    assert len(token) == 32 and all(c in "0123456789abcdef" for c in token)


async def test_lock_acquired_before_warmup(engine, session, fake_redis):
    """The lock's SET strictly precedes any infer() call (warm-up)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    class Logged(FakeInference):
        def __init__(self, events):
            super().__init__()
            self.events = events

        async def infer(self, model, system_prompt, user_prompt):
            self.events.append("infer")
            return await super().infer(model, system_prompt, user_prompt)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_inference(
        str(job.id), session_factory=factory, inference=Logged(fake_redis.events), redis=fake_redis
    )
    assert fake_redis.events.index("set") < fake_redis.events.index("infer")


async def test_lock_busy_writes_waiting_model_and_stays_pending(engine, session, fake_redis):
    """Busy model -> ModelBusy; job PENDING, result_json.phase = waiting_model."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    fake_redis.store["inference:lock:OLLAMA:x"] = "token-autre-job"

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(ModelBusy):
        await _run_inference(
            str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.PENDING
    assert job.result_json == {"phase": "waiting_model"}


async def test_lock_busy_retries_task_countdown_30(monkeypatch):
    """Wrapper : ModelBusy → retry Celery countdown=30, max_retries=_MODEL_BUSY_MAX_RETRIES."""

    async def _busy(*args, **kwargs):
        raise ModelBusy("inference:lock:OLLAMA:x")

    monkeypatch.setattr(inference_task_module, "_run_inference", _busy)
    task = inference_task._get_current_object()

    class FakeSig:
        options = {"countdown": 30, "retries": 1}

        def apply_async(self, *args, **kwargs):
            return None  # pas de resoumission réelle en test

    monkeypatch.setattr(task, "signature_from_request", lambda *args, **kwargs: FakeSig())

    def _invoke():
        task.push_request(retries=0, called_directly=False)
        try:
            inference_task(str(uuid.uuid4()))
        finally:
            task.pop_request()

    # asyncio.run (in the wrapper) is impossible from a running event loop
    with pytest.raises(Retry) as exc_info:
        await asyncio.to_thread(_invoke)
    assert exc_info.value.when == 30  # countdown
    assert exc_info.value.sig.options["countdown"] == 30
    assert task.override_max_retries == inference_task_module._MODEL_BUSY_MAX_RETRIES


async def test_lock_busy_exhausted_retries_fails_job(engine, session, monkeypatch):
    """Wrapper: retries exhausted -> job FAILED (readable error, completed_at set)."""

    async def _busy(*args, **kwargs):
        raise ModelBusy("inference:lock:OLLAMA:x")

    monkeypatch.setattr(inference_task_module, "_run_inference", _busy)
    monkeypatch.setattr(
        inference_task_module,
        "_session_factory_override",
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
    )
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    task = inference_task._get_current_object()

    def _invoke():
        task.push_request(
            retries=inference_task_module._MODEL_BUSY_MAX_RETRIES, called_directly=False
        )
        try:
            inference_task(str(job.id))
        finally:
            task.pop_request()

    with pytest.raises(MaxRetriesExceededError):
        await asyncio.to_thread(_invoke)
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert "modèle occupé" in job.error and "waiting_model" in job.error
    assert job.completed_at is not None


async def test_lock_released_after_success(engine, session, fake_redis):
    """Job COMPLETED -> lock released (key deleted)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_inference(
        str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
    )
    assert fake_redis.set_calls[0][0] not in fake_redis.store


async def test_lock_released_on_inference_error(engine, session, fake_redis):
    """infer() raises InferenceError -> job FAILED and lock released (finally path)."""

    class Down(FakeInference):
        async def infer(self, model, system_prompt, user_prompt):
            raise InferenceError("down")

    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(InferenceError):
        await _run_inference(
            str(job.id), session_factory=factory, inference=Down(), redis=fake_redis
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert fake_redis.set_calls[0][0] not in fake_redis.store


async def test_release_only_own_token(fake_redis):
    """Owner check: the release does not delete a key carrying another token."""
    key = "inference:lock:OLLAMA:x"
    await fake_redis.set(key, "token-proprire")
    fake_redis.store[key] = "token-autre"  # lock expiré puis repris
    await _release_lock(fake_redis, key, "token-proprire")
    assert fake_redis.store.get(key) == "token-autre"
    await _release_lock(fake_redis, key, "token-autre")
    assert key not in fake_redis.store


async def test_no_lock_for_openai_api_model(engine, session, fake_redis):
    """OPENAI_API -> no SET, no release; job COMPLETED as today."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    session.add(
        ModelRegistry(
            name="GPT",
            model_identifier="https://llm.test/v1#gpt-4o-mini",
            backend_type="OPENAI_API",
        )
    )
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="GPT",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 0
    assert fake_redis.set_calls == []
    assert fake_redis.store == {}


async def test_no_lock_when_model_not_in_registry(engine, session, fake_redis):
    """model_used absent from the registry -> no lock, path unchanged."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Inconnu",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 0
    assert fake_redis.set_calls == []
    assert fake_redis.store == {}


async def test_redelivered_closed_job_does_not_relock(engine, session, fake_redis):
    """Job already FAILED -> return {} before the lock (no SET)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.FAILED,
        error="cancelled",
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    assert (
        await _run_inference(
            str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
        )
        == {}
    )
    assert fake_redis.set_calls == []


# =====================================================================================
# Correctifs revue OLLAMA (spec 2026-10-29-correctifs-revue-ollama)
# =====================================================================================


async def _project_prompt(session):
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    return project, prompt


async def _job(session, project, prompt, main: str, pre: str | None = None) -> InferenceJob:
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used=main,
        prefilter_model=pre,
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    return job


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class ByModel(FakeInference):
    """Response per model identifier: dict (prediction) or exception to raise."""

    def __init__(self, answers: dict, events: list | None = None):
        super().__init__()
        self.answers = answers
        self.events = events

    async def infer(self, model, system_prompt, user_prompt):
        self.calls += 1
        if self.events is not None:
            self.events.append("infer")
        out = self.answers.get(model, {"ok": True})
        if isinstance(out, Exception):
            raise out
        return dict(out)


def _llm_transport(answers: dict):
    """Real OpenAICompatibleService behind httpx.MockTransport: answers per requested
    model in both formats (/api/chat and /v1); logs (path, body) of each call."""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        content = json.dumps(answers.get(body["model"], {"ok": True}))
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": content},
                "choices": [{"message": {"content": content}}],
            },
        )

    return calls, httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- §2: native endpoint / /v1 and extra body decided PER MODEL (real service) ---


async def test_mixed_job_endpoint_per_model_real_service(engine, session, fake_redis):
    """OLLAMA pre-filter + LLAMA_CPP main, real service: each call (warm-ups included)
    goes to ITS model's endpoint — /api/chat for OLLAMA, /v1 for LLAMA_CPP."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="qwen3:1.7b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Big", model_identifier="llama-big", backend_type="LLAMA_CPP"))
    await session.commit()
    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "Big", "Small")
    calls, client = _llm_transport(
        {"qwen3:1.7b": {"confidence_score": 0.5}, "llama-big": {"confidence_score": 0.9}}
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1
    assert {(b["model"], p) for p, b in calls} == {
        ("qwen3:1.7b", "/api/chat"),
        ("llama-big", "/v1/chat/completions"),
    }


async def test_prefilter_ollama_native_with_extra_body_when_main_is_openai_api(
    engine, session, fake_redis
):
    """OPENAI_API main + OLLAMA pre-filter: the pre-filter keeps /api/chat AND the
    extra body (num_ctx, think) — it does not inherit the main model's settings."""
    project, prompt = await _project_prompt(session)
    session.add(
        ModelRegistry(
            name="GPT", model_identifier="https://llm.ext/v1#gpt", backend_type="OPENAI_API"
        )
    )
    session.add(ModelRegistry(name="Small", model_identifier="qwen3:1.7b", backend_type="OLLAMA"))
    await session.commit()
    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "GPT", "Small")
    # stage 1 rejects: the main model (remote) is never called -> 100% offline
    calls, client = _llm_transport({"qwen3:1.7b": {"confidence_score": 0.05}})
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["prefiltered"] == 1
    pre = [(p, b) for p, b in calls if b["model"] == "qwen3:1.7b"]
    assert pre
    assert all(p == "/api/chat" for p, _ in pre)
    assert all(b.get("options", {}).get("num_ctx") == 8192 for _, b in pre)
    assert all(b.get("think") is False for _, b in pre)


# --- §3: per-model lock — renewal, errors, TTL, waiting, pre-filter ---


async def test_lock_renewed_before_each_model_call(engine, session, fake_redis):
    """The TTL (timeout + 300 s) bounds ONE call, not the job: renewed (EXPIRE, same TTL)
    just before each model call, warm-up included."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=2)
    job = await _job(session, project, prompt, "Kev-4B")
    fake = ByModel({"x": {"confidence_score": 0.9}}, events=fake_redis.events)
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=fake, redis=fake_redis
    )
    assert result["processed"] == 2
    ev = fake_redis.events
    infers = [i for i, e in enumerate(ev) if e == "infer"]
    assert len(infers) == 3  # 1 warm-up + 2 articles
    assert all(ev[i - 1] == "expire" for i in infers)
    assert fake_redis.expire_calls and all(
        c == ("inference:lock:OLLAMA:x", 420) for c in fake_redis.expire_calls
    )


class DownRedis:
    """Unreachable Redis: SET in error (message with internal host:port)."""

    async def set(self, *args, **kwargs):
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("Error 111 connecting to redis:6379")

    async def get(self, key):
        return None

    async def delete(self, key):
        return 0

    async def expire(self, key, seconds):
        return False

    async def aclose(self):
        pass


async def test_lock_redis_error_marks_job_failed(engine, session):
    """Redis in error at acquisition: job FAILED (not PENDING for life, which would block the
    "one job per project" guard), generic message (never host:port in job.error)."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Kev-4B")
    with pytest.raises(RuntimeError, match="model_lock_unavailable"):
        await _run_inference(
            str(job.id),
            session_factory=_factory(engine),
            inference=FakeInference(),
            redis=DownRedis(),
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert job.error == "model_lock_unavailable"
    assert "redis" not in job.error and "6379" not in job.error
    assert job.completed_at is not None


async def test_lock_redis_error_on_prefilter_key_releases_main_lock(engine, session, fake_redis):
    """Redis error on the 2nd key: the 1st lock already taken is released, job FAILED."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    class FlakyRedis(type(fake_redis)):
        async def set(self, key, value, nx=False, ex=None):
            if key.endswith("small:1b"):
                raise RedisConnectionError("Error 111 connecting to redis:6379")
            return await super().set(key, value, nx=nx, ex=ex)

    redis = FlakyRedis()
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Big", "Small")
    with pytest.raises(RuntimeError, match="model_lock_unavailable"):
        await _run_inference(
            str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=redis
        )
    assert redis.store == {}  # le lock du principal n'a pas fui
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert job.error == "model_lock_unavailable"


@pytest.mark.parametrize("main,pre", [("Big", "Small"), ("Small", "Big")])
async def test_lock_keys_acquired_in_deterministic_order(engine, session, fake_redis, main, pre):
    """SET order independent of (main, pre-filter): no crossed livelock
    between two jobs with inverted roles."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, main, pre)
    await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert [c[0] for c in fake_redis.set_calls] == [
        "inference:lock:OLLAMA:big:8b",
        "inference:lock:OLLAMA:small:1b",
    ]


async def test_lock_renewal_covers_both_keys_with_prefilter(engine, session, fake_redis):
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "Big", "Small")
    await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert {k for k, _ in fake_redis.expire_calls} == {
        "inference:lock:OLLAMA:big:8b",
        "inference:lock:OLLAMA:small:1b",
    }


async def test_heartbeat_lost_token_does_not_renew_nor_delete_foreign_lock(
    engine, session, fake_redis
):
    """Lock taken over by another job mid-way: no EXPIRE, no DEL of the
    foreign lock (release included), the job continues."""
    key = "inference:lock:OLLAMA:x"

    class Thief(FakeInference):
        async def infer(self, model, system_prompt, user_prompt):
            fake_redis.store[key] = "token-autre-job"  # expiré puis repris
            return await super().infer(model, system_prompt, user_prompt)

    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=2)
    job = await _job(session, project, prompt, "Kev-4B")
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=Thief(), redis=fake_redis
    )
    assert result["processed"] == 2
    assert len(fake_redis.expire_calls) == 1  # seul le heartbeat avant le warm-up renouvelle
    assert fake_redis.store[key] == "token-autre-job"
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_heartbeat_redis_error_lets_job_continue(engine, session, fake_redis):
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def boom(key, seconds):
        raise RedisConnectionError("Error 111 connecting to redis:6379")

    fake_redis.expire = boom
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=2)
    job = await _job(session, project, prompt, "Kev-4B")
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 2
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    assert fake_redis.store == {}


async def test_running_commit_failure_marks_job_failed(engine, session, fake_redis):
    """RUNNING commit failure (DB): job FAILED, lock released."""

    class BoomSession(AsyncSession):
        async def commit(self):
            if any(
                isinstance(o, InferenceJob) and o.status == InferenceJobStatus.RUNNING
                for o in self.dirty
            ):
                raise RuntimeError("db down")
            await super().commit()

    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Kev-4B")
    factory = async_sessionmaker(engine, class_=BoomSession, expire_on_commit=False)
    with pytest.raises(RuntimeError, match="db down"):
        await _run_inference(
            str(job.id), session_factory=factory, inference=FakeInference(), redis=fake_redis
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert fake_redis.store == {}


async def test_lock_ttl_non_finite_timeout_falls_back(engine, session, fake_redis):
    """inference_timeout = 'inf': TTL = 120 + 300 (same parser as the factory), no
    OverflowError."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await set_setting(session, "inference_timeout", "inf")
    job = await _job(session, project, prompt, "Kev-4B")
    await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert fake_redis.set_calls[0][3] == 420


async def test_lock_busy_keeps_waiting_beyond_40_retries(engine, session, monkeypatch):
    """Waiting bounded by a job's horizon (24 h, visibility_timeout), not 20 min: at the 40th
    attempt the job is still requeued and stays PENDING."""

    async def _busy(*args, **kwargs):
        raise ModelBusy("inference:lock:OLLAMA:x")

    monkeypatch.setattr(inference_task_module, "_run_inference", _busy)
    monkeypatch.setattr(inference_task_module, "_session_factory_override", _factory(engine))
    project, prompt = await _project_prompt(session)
    job = await _job(session, project, prompt, "Kev-4B")
    task = inference_task._get_current_object()

    class FakeSig:
        options = {"countdown": 30, "retries": 41}
        args, kwargs = (), {}

        def apply_async(self, *args, **kwargs):
            return None

    monkeypatch.setattr(task, "signature_from_request", lambda *args, **kwargs: FakeSig())

    def _invoke():
        task.push_request(retries=40, called_directly=False)
        try:
            inference_task(str(job.id))
        finally:
            task.pop_request()

    with pytest.raises(Retry):
        await asyncio.to_thread(_invoke)
    await session.refresh(job)
    assert job.status == InferenceJobStatus.PENDING
    horizon = (
        inference_task_module._MODEL_BUSY_MAX_RETRIES
        * inference_task_module._MODEL_BUSY_RETRY_SECONDS
    )
    assert horizon >= 24 * 3600


async def test_lock_also_taken_on_prefilter_model(engine, session, fake_redis):
    """Local pre-filter: locked too (own key), then released with the main one."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Big", "Small")
    await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    keys = {c[0] for c in fake_redis.set_calls}
    assert keys == {"inference:lock:OLLAMA:big:8b", "inference:lock:OLLAMA:small:1b"}
    assert fake_redis.store == {}  # les deux relâchés


async def test_lock_prefilter_busy_releases_main_and_waits(engine, session, fake_redis):
    """All-or-nothing: busy pre-filter -> the main lock is released, job PENDING
    waiting_model, ModelBusy."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Big", "Small")
    fake_redis.store["inference:lock:OLLAMA:small:1b"] = "token-autre-job"
    with pytest.raises(ModelBusy):
        await _run_inference(
            str(job.id),
            session_factory=_factory(engine),
            inference=FakeInference(),
            redis=fake_redis,
        )
    assert "inference:lock:OLLAMA:big:8b" not in fake_redis.store
    await session.refresh(job)
    assert job.status == InferenceJobStatus.PENDING
    assert job.result_json == {"phase": "waiting_model"}


async def test_lock_prefilter_same_model_single_key(engine, session, fake_redis):
    """Guard: pre-filter and main on the same local model -> a single key (no
    self-locking)."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="qwen3:4b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Alias", model_identifier="qwen3:4b", backend_type="OLLAMA"))
    await session.commit()
    job = await _job(session, project, prompt, "Big", "Alias")
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 0
    assert [c[0] for c in fake_redis.set_calls] == ["inference:lock:OLLAMA:qwen3:4b"]


# --- §4: small model outputs (invalid JSON, non-numeric confidence) ---


@pytest.mark.parametrize(
    "value",
    [
        "high",
        [0.9],
        {"v": 0.9},
        True,
        float("nan"),
        85,  # hors [0, 1] (échelle 0-100) : jamais auto-accepté
        "85",
        -0.1,
        1.5,
        "inf",
        "nan",
        "1e999",
        10**400,  # float(int) → OverflowError, pas d'exception
    ],
)
def test_triage_non_numeric_confidence_needs_review(value):
    assert _triage(value) == PredictionStatus.NEEDS_HUMAN_REVIEW


def test_triage_numeric_string_coerced():
    assert _triage("0.9") == PredictionStatus.AUTO_ACCEPTED
    assert _triage(" 0.05 ") == PredictionStatus.AUTO_REJECTED


async def test_run_inference_non_numeric_confidence_completes(engine, session, fake_redis):
    """Non-numeric confidence_score: job COMPLETED, article in re-read, stored score None."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "Kev-4B")
    result = await _run_inference(
        str(job.id),
        session_factory=_factory(engine),
        inference=FakeInference(confidence="high"),
        redis=fake_redis,
    )
    assert result["needs_review"] == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert pred.status == PredictionStatus.NEEDS_HUMAN_REVIEW
    assert pred.confidence_score is None


async def test_prefilter_invalid_json_falls_through_to_main(engine, session, fake_redis):
    """Stage 1 non-JSON output: the article goes to the main model (not skipped)."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big:8b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Small", model_identifier="small:1b", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "Big", "Small")
    fake = ByModel(
        {"small:1b": InferenceError("invalid_json"), "big:8b": {"confidence_score": 0.9}}
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=fake, redis=fake_redis
    )
    assert result["processed"] == 1 and result["invalid_json"] == 0
    assert result["auto_accepted"] == 1 and result["prefiltered"] == 0
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert pred.model_used == "Big"


# =====================================================================================
# Model traceability + version (spec 2026-10-29-tracabilite-modele-version §2)
# =====================================================================================

DIGEST_SMALL = "1" * 12 + "a" * 52
DIGEST_BIG = "2" * 12 + "b" * 52


def _tags(*entries: tuple[str, str]) -> dict:
    return {
        "models": [
            {
                "name": name,
                "model": name,
                "digest": digest,
                "size": 1234,
                "modified_at": "2026-10-01T08:00:00Z",
                "details": {"parameter_size": "4.0B", "quantization_level": "Q4_K_M"},
            }
            for name, digest in entries
        ]
    }


def _tags_transport(answers: dict, tags=None, tags_exc=None, served: dict | None = None):
    """Like ``_llm_transport`` + ``GET /api/tags`` (``tags``: JSON dict or httpx.Response;
    ``tags_exc``: raised exception); ``served``: ``model`` field returned per requested model.
    Logs the GET paths in ``gets``."""
    calls: list[tuple[str, dict]] = []
    gets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            gets.append(request.url.path)
            if tags_exc is not None:
                raise tags_exc
            if isinstance(tags, httpx.Response):
                return tags
            return httpx.Response(200, json=tags if tags is not None else {"models": []})
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        content = json.dumps(answers.get(body["model"], {"ok": True}))
        out = {
            "message": {"role": "assistant", "content": content},
            "choices": [{"message": {"content": content}}],
        }
        if served and body["model"] in served:
            out["model"] = served[body["model"]]
        return httpx.Response(200, json=out)

    return calls, gets, httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _ollama_job(session, project, prompt, main="Big", pre=None):
    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    await _seed(session, project, prompt, None, n_articles=1)
    return await _job(session, project, prompt, main, pre)


async def test_job_snapshot_and_prediction_version_ollama_real_service(engine, session, fake_redis):
    """OLLAMA: job snapshot (digest + details, read from /api/tags) and each prediction
    carries the identifier and digest of the model that produced it."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="qwen3:8b", backend_type="OLLAMA"))
    await session.commit()
    job = await _ollama_job(session, project, prompt)
    _, gets, client = _tags_transport(
        {"qwen3:8b": {"confidence_score": 0.9}}, tags=_tags(("qwen3:8b", DIGEST_BIG))
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1
    assert "/api/tags" in gets
    await session.refresh(job)
    main = job.model_info["main"]
    assert (main["name"], main["backend"], main["identifier"]) == ("Big", "OLLAMA", "qwen3:8b")
    assert main["version"] == DIGEST_BIG and main["quantization"] == "Q4_K_M"
    assert job.model_info["prefilter"] is None
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert (pred.model_used, pred.model_identifier, pred.model_version) == (
        "Big",
        "qwen3:8b",
        DIGEST_BIG,
    )


async def test_prefilter_rejected_prediction_carries_prefilter_identifier_and_version(
    engine, session, fake_redis
):
    """Rejection at stage 1: the prediction carries the pre-filter model (name, identifier,
    digest); the job snapshot covers both stages."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="qwen3:1.7b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Big", model_identifier="qwen3:8b", backend_type="OLLAMA"))
    await session.commit()
    job = await _ollama_job(session, project, prompt, "Big", "Small")
    _, _, client = _tags_transport(
        {"qwen3:1.7b": {"confidence_score": 0.05}},
        tags=_tags(("qwen3:1.7b", DIGEST_SMALL), ("qwen3:8b", DIGEST_BIG)),
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["prefiltered"] == 1
    await session.refresh(job)
    assert job.model_info["prefilter"]["version"] == DIGEST_SMALL
    assert job.model_info["main"]["version"] == DIGEST_BIG
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert (pred.model_used, pred.model_identifier, pred.model_version) == (
        "Small",
        "qwen3:1.7b",
        DIGEST_SMALL,
    )


@pytest.mark.parametrize(
    "tags, exc",
    [
        (httpx.Response(500, text="boom"), None),
        (httpx.Response(200, text="<html/>"), None),
        (None, httpx.ConnectError("refused")),
        ({"models": [{"name": "autre:1b", "digest": "e" * 64}]}, None),
    ],
    ids=["http-500", "non-json", "connection-error", "tag-absent"],
)
async def test_snapshot_failure_never_fails_job(engine, session, fake_redis, tags, exc):
    """Version reading impossible: the job completes, version None, identifier kept."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="qwen3:8b", backend_type="OLLAMA"))
    await session.commit()
    job = await _ollama_job(session, project, prompt)
    _, _, client = _tags_transport({"qwen3:8b": {"confidence_score": 0.9}}, tags, exc)
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    assert job.model_info["main"]["identifier"] == "qwen3:8b"
    assert job.model_info["main"]["version"] is None
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert pred.model_identifier == "qwen3:8b" and pred.model_version is None


async def test_local_v1_backend_version_is_served_model_echo(engine, session, fake_redis):
    """LLAMA_CPP (/v1): no /api/tags; the version is the ``model`` returned by the server,
    on the prediction and in the job snapshot."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="llama-big", backend_type="LLAMA_CPP"))
    await session.commit()
    job = await _ollama_job(session, project, prompt)
    _, gets, client = _tags_transport(
        {"llama-big": {"confidence_score": 0.9}},
        served={"llama-big": "models/llama-big-Q4_K_M.gguf"},
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1
    assert gets == []
    await session.refresh(job)
    assert job.model_info["main"]["backend"] == "LLAMA_CPP"
    assert job.model_info["main"]["version"] == "models/llama-big-Q4_K_M.gguf"
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert pred.model_identifier == "llama-big"
    assert pred.model_version == "models/llama-big-Q4_K_M.gguf"


async def test_openai_api_job_traces_served_model_without_url_secrets(
    engine, session, fake_redis, monkeypatch
):
    """OPENAI_API: identifier = model part, endpoint without secret, version = resolved alias
    returned by the API; no userinfo, no query, no key in the snapshot or the prediction."""
    from app.core.crypto import encrypt_secret

    project, prompt = await _project_prompt(session)
    session.add(
        ModelRegistry(
            name="GPT",
            model_identifier="https://bob:pw-SECRET@api.x.test/v1?key=SECRET-Q#gpt-4o",
            backend_type="OPENAI_API",
            api_key_enc=encrypt_secret("sk-SECRET-KEY"),
        )
    )
    await session.commit()
    job = await _ollama_job(session, project, prompt, "GPT")
    _, _, client = _tags_transport(
        {"gpt-4o": {"confidence_score": 0.9}}, served={"gpt-4o": "gpt-4o-2024-08-06"}
    )
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=client._transport, **kw))
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1
    await session.refresh(job)
    main = job.model_info["main"]
    assert (main["identifier"], main["endpoint"]) == ("gpt-4o", "https://api.x.test")
    assert main["version"] == "gpt-4o-2024-08-06"
    assert "SECRET" not in json.dumps(job.model_info) and "bob" not in json.dumps(job.model_info)
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert (pred.model_identifier, pred.model_version) == ("gpt-4o", "gpt-4o-2024-08-06")


async def test_injected_fake_service_records_identifier_without_version(
    engine, session, fake_redis
):
    """Injected service (tests, no HTTP): identifier traced, version None, full job."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b", backend_type="OLLAMA"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=1)
    job = await _job(session, project, prompt, "Kev-4B")
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), inference=FakeInference(), redis=fake_redis
    )
    assert result["processed"] == 1
    await session.refresh(job)
    assert job.model_info["main"]["identifier"] == "kef/kev-4b"
    assert job.model_info["main"]["version"] is None
    pred = (await session.execute(select(ArticlePrediction))).scalars().one()
    assert pred.model_identifier == "kef/kev-4b" and pred.model_version is None


# --- Review: versions per stage, multi-article job (spec §2, D4) ---


def _served_transport(contents: dict, served):
    """Real service behind MockTransport: ``contents`` = RAW content returned per model
    (str, unencoded); ``served(n, model)`` = ``model`` field returned at call n° ``n``
    (0 = warm-up, then one per real call) or None (field absent)."""
    n = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        body = json.loads(request.content)
        content = contents[body["model"]]
        out = {
            "message": {"role": "assistant", "content": content},
            "choices": [{"message": {"content": content}}],
        }
        echo = served(n, body["model"])
        n += 1
        if echo is not None:
            out["model"] = echo
        return httpx.Response(200, json=out)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _pred_tuple(session):
    p = (await session.execute(select(ArticlePrediction))).scalars().one()
    return p.model_used, p.model_identifier, p.model_version


async def test_prefilter_passes_prediction_carries_main_identifier_and_version(
    engine, session, fake_redis
):
    """D4: the pre-filter does not reject -> the prediction carries the main model (identifier
    + digest), not the pre-filter's."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="qwen3:1.7b", backend_type="OLLAMA"))
    session.add(ModelRegistry(name="Big", model_identifier="qwen3:8b", backend_type="OLLAMA"))
    await session.commit()
    job = await _ollama_job(session, project, prompt, "Big", "Small")
    _, _, client = _tags_transport(
        {"qwen3:1.7b": {"confidence_score": 0.9}, "qwen3:8b": {"confidence_score": 0.9}},
        tags=_tags(("qwen3:1.7b", DIGEST_SMALL), ("qwen3:8b", DIGEST_BIG)),
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["prefiltered"] == 0
    assert await _pred_tuple(session) == ("Big", "qwen3:8b", DIGEST_BIG)


async def test_prefilter_invalid_json_prediction_carries_main_served_echo(
    engine, session, fake_redis
):
    """D4: unreadable stage 1 -> stage 2; the version comes from the stage 2 echo (the stage 1
    ``meta`` is not reused)."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="small-id", backend_type="LLAMA_CPP"))
    session.add(ModelRegistry(name="Big", model_identifier="big-id", backend_type="LLAMA_CPP"))
    await session.commit()
    job = await _ollama_job(session, project, prompt, "Big", "Small")
    client = _served_transport(
        {"small-id": "pas du json", "big-id": json.dumps({"confidence_score": 0.9})},
        lambda n, model: {"small-id": "small-echo", "big-id": "big-echo"}[model],
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 1 and result["prefiltered"] == 0
    assert await _pred_tuple(session) == ("Big", "big-id", "big-echo")


async def test_non_ollama_prefilter_rejecting_prediction_carries_prefilter_served_version(
    engine, session, fake_redis
):
    """D4: rejection by a non-Ollama pre-filter -> pre-filter identifier + its echo."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="small-id", backend_type="LLAMA_CPP"))
    session.add(ModelRegistry(name="Big", model_identifier="big-id", backend_type="LLAMA_CPP"))
    await session.commit()
    job = await _ollama_job(session, project, prompt, "Big", "Small")
    client = _served_transport(
        {"small-id": json.dumps({"confidence_score": 0.05}), "big-id": "{}"},
        lambda n, model: {"small-id": "small-echo", "big-id": "big-echo"}[model],
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["prefiltered"] == 1
    assert await _pred_tuple(session) == ("Small", "small-id", "small-echo")


async def test_multi_article_non_ollama_versions_per_prediction_job_keeps_first(
    engine, session, fake_redis
):
    """Non-Ollama 3-article job: version PER prediction (None, v2, v3); the job
    snapshot keeps the first non-null version, and it survives the following commits."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Big", model_identifier="big-id", backend_type="LLAMA_CPP"))
    await session.commit()
    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    await _seed(session, project, prompt, None, n_articles=3)
    job = await _job(session, project, prompt, "Big")
    echoes = {0: "warm", 1: None, 2: "v2", 3: "v3"}  # 0 = warm-up
    client = _served_transport(
        {"big-id": json.dumps({"confidence_score": 0.9})}, lambda n, model: echoes[n]
    )
    result = await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    assert result["processed"] == 3
    preds = (
        (await session.execute(select(ArticlePrediction).order_by(ArticlePrediction.created_at)))
        .scalars()
        .all()
    )
    assert sorted(p.model_version or "" for p in preds) == ["", "v2", "v3"]
    await session.refresh(job)
    assert job.model_info["main"]["version"] == "v2"


async def test_non_ollama_prefilter_never_rejecting_still_fills_job_snapshot_version(
    engine, session, fake_redis
):
    """Non-Ollama pre-filter answering on all articles without ever rejecting: its
    served version still fills the job snapshot (it produced no prediction)."""
    project, prompt = await _project_prompt(session)
    session.add(ModelRegistry(name="Small", model_identifier="small-id", backend_type="LLAMA_CPP"))
    session.add(ModelRegistry(name="Big", model_identifier="big-id", backend_type="LLAMA_CPP"))
    await session.commit()
    job = await _ollama_job(session, project, prompt, "Big", "Small")
    client = _served_transport(
        {"small-id": json.dumps({"confidence_score": 0.9}), "big-id": "{}"},
        lambda n, model: {"small-id": "small-echo", "big-id": None}[model],
    )
    await _run_inference(
        str(job.id), session_factory=_factory(engine), http=client, redis=fake_redis
    )
    await session.refresh(job)
    assert job.model_info["prefilter"]["version"] == "small-echo"
    assert job.model_info["main"]["version"] is None
    assert (await _pred_tuple(session))[2] is None  # la prédiction (étage 2) : pas d'écho
