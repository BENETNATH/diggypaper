"""Tests de la task inférence massive (app/tasks/inference.py) — 100 % offline."""

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
    """Inference mockée : renvoie une prédiction fixe (confidence configurable)."""

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
    # predictions créées
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(preds) == 2
    assert all(p.status == PredictionStatus.AUTO_ACCEPTED for p in preds)


async def test_run_inference_triage_mixed(engine, session, fake_redis):
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
        await _run_inference(
            str(job.id), session_factory=factory, inference=FailInference(), redis=fake_redis
        )
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED


async def test_run_inference_stops_when_cancelled(engine, session, fake_redis):
    """Arrêt demandé en cours de route : on s'arrête avant l'article suivant, sans e-mail."""
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
    # warm-up + 1 article traité, puis arrêt
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
    """Modèle local : un seul warm-up (absorbe le chargement des poids), pas un par article."""
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
    """Backend distant (OPENAI_API) : pas de chargement local → pas de warm-up."""
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
    # 0 article : la boucle n'appelle jamais infer_with (reste 100 % offline)
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


# --- Lock par modèle (Redis) ---


async def test_lock_key_ttl_and_token(engine, session, fake_redis):
    """Clé inference:lock:<backend>:<identifiant>, SET NX, TTL = timeout + 300 s, token hex."""
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
    """Le SET du lock précède strictement tout appel infer() (warm-up)."""
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
    """Modèle occupé → ModelBusy ; job PENDING, result_json.phase = waiting_model."""
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

    # asyncio.run (dans le wrapper) impossible depuis une event loop en cours
    with pytest.raises(Retry) as exc_info:
        await asyncio.to_thread(_invoke)
    assert exc_info.value.when == 30  # countdown
    assert exc_info.value.sig.options["countdown"] == 30
    assert task.override_max_retries == inference_task_module._MODEL_BUSY_MAX_RETRIES


async def test_lock_busy_exhausted_retries_fails_job(engine, session, monkeypatch):
    """Wrapper : retries épuisées → job FAILED (erreur lisible, completed_at posée)."""

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
    """Job COMPLETED → lock relâché (clé supprimée)."""
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
    """infer() lève InferenceError → job FAILED et lock relâché (chemin finally)."""

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
    """Owner check : la release ne supprime pas une clé qui porte un autre token."""
    key = "inference:lock:OLLAMA:x"
    await fake_redis.set(key, "token-proprire")
    fake_redis.store[key] = "token-autre"  # lock expiré puis repris
    await _release_lock(fake_redis, key, "token-proprire")
    assert fake_redis.store.get(key) == "token-autre"
    await _release_lock(fake_redis, key, "token-autre")
    assert key not in fake_redis.store


async def test_no_lock_for_openai_api_model(engine, session, fake_redis):
    """OPENAI_API → aucun SET, aucune release ; job COMPLETED comme aujourd'hui."""
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
    """model_used absent du registre → pas de lock, chemin inchangé."""
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
    """Job déjà FAILED → return {} avant le lock (aucun SET)."""
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
    """Réponse par identifiant de modèle : dict (prédiction) ou exception à lever."""

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
    """Vrai OpenAICompatibleService derrière httpx.MockTransport : répond selon le modèle
    demandé aux deux formats (/api/chat et /v1) ; journalise (chemin, corps) de chaque appel."""
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


# --- §2 : endpoint natif / /v1 et corps additionnel décidés PAR MODÈLE (vrai service) ---


async def test_mixed_job_endpoint_per_model_real_service(engine, session, fake_redis):
    """Pré-filtre OLLAMA + principal LLAMA_CPP, service réel : chaque appel (warm-ups compris)
    part sur l'endpoint de SON modèle — /api/chat pour OLLAMA, /v1 pour LLAMA_CPP."""
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
    """Principal OPENAI_API + pré-filtre OLLAMA : le pré-filtre garde /api/chat ET le corps
    additionnel (num_ctx, think) — il n'hérite pas des réglages du modèle principal."""
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
    # étage 1 rejette : le modèle principal (distant) n'est jamais appelé → 100 % offline
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


# --- §3 : lock par modèle — renouvellement, erreurs, TTL, attente, pré-filtre ---


async def test_lock_renewed_before_each_model_call(engine, session, fake_redis):
    """Le TTL (timeout + 300 s) borne UN appel, pas le job : renouvelé (EXPIRE, même TTL)
    juste avant chaque appel modèle, warm-up compris."""
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
    """Redis injoignable : SET en erreur (message avec host:port interne)."""

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
    """Redis en erreur à l'acquisition : job FAILED (pas PENDING à vie, qui bloquerait la
    garde « un job par projet »), message générique (jamais host:port dans job.error)."""
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
    """Erreur Redis sur la 2e clé : le 1er lock déjà pris est relâché, job FAILED."""
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
    """Ordre des SET indépendant de (principal, pré-filtre) : pas de livelock croisé
    entre deux jobs aux rôles inversés."""
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
    """Lock repris par un autre job en cours de route : pas d'EXPIRE, pas de DEL du lock
    étranger (release compris), le job continue."""
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
    """Échec du commit RUNNING (DB) : job FAILED, lock relâché."""

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
    """inference_timeout = 'inf' : TTL = 120 + 300 (même parseur que la fabrique), pas
    d'OverflowError."""
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
    """Attente bornée par l'horizon d'un job (24 h, visibility_timeout), pas 20 min : à la 40e
    tentative le job est encore requeue et reste PENDING."""

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
    """Pré-filtre local : locké lui aussi (clé propre), puis relâché avec le principal."""
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
    """Tout-ou-rien : pré-filtre occupé → le lock du principal est relâché, job PENDING
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
    """Garde : pré-filtre et principal sur le même modèle local → une seule clé (pas
    d'auto-blocage)."""
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


# --- §4 : sorties de petits modèles (JSON invalide, confiance non numérique) ---


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
    """confidence_score non numérique : job COMPLETED, article en relecture, score stocké None."""
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
    """Sortie non-JSON de l'étage 1 : l'article passe au modèle principal (pas écarté)."""
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
