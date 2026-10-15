"""Tests de la task benchmark (app/tasks/benchmark.py) — 100 % offline."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.tasks.benchmark import _compute_metrics, _run_benchmark


class FakeInference:
    """Inference mockée : renvoie une prédiction fixe."""

    def __init__(self, prediction: dict):
        self._prediction = prediction
        self.calls = 0

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.calls += 1
        return dict(self._prediction)


def _gt(use_ai=True, sex="BOTH_SEXES", conf=0.9, quotes=("mice were randomized",)):
    return {
        "use_ai_behavior": use_ai,
        "animal_sex": sex,
        "confidence_score": conf,
        "evidence_quotes": list(quotes),
        "reasoning_summary": "ok",
    }


# --- _compute_metrics ---


def test_metrics_exact_match():
    gt = _gt()
    m = _compute_metrics(gt, gt)
    assert m["accuracy"] == 1.0
    assert m["precision_score"] == 1.0
    assert m["recall_score"] == 1.0
    assert m["f1_score"] == 1.0


def test_metrics_partial_quotes():
    gt = _gt(quotes=("a", "b", "c"))
    pred = _gt(quotes=("a", "b", "x"))
    m = _compute_metrics(pred, gt)
    # intersection {a,b} = 2 ; precision 2/3, recall 2/3
    assert m["precision_score"] == pytest.approx(2 / 3)
    assert m["recall_score"] == pytest.approx(2 / 3)
    assert m["f1_score"] == pytest.approx(2 / 3)


def test_metrics_wrong_scalar():
    gt = _gt(use_ai=True)
    pred = _gt(use_ai=False)
    m = _compute_metrics(pred, gt)
    # 2/3 scalaires exacts (animal_sex + confidence_score)
    assert m["accuracy"] == pytest.approx(2 / 3)


def test_metrics_empty_gold_quotes():
    gt = _gt(quotes=())
    pred = _gt(quotes=("a",))
    m = _compute_metrics(pred, gt)
    # gold vide → precision/recall/f1 = 1.0 (rien à extraire)
    assert m["precision_score"] == 1.0
    assert m["recall_score"] == 1.0
    assert m["f1_score"] == 1.0


def test_metrics_pred_empty_quotes():
    gt = _gt(quotes=("a",))
    pred = _gt(quotes=())
    m = _compute_metrics(pred, gt)
    assert m["precision_score"] == 0.0
    assert m["recall_score"] == 0.0
    assert m["f1_score"] == 0.0


# --- _run_benchmark ---


async def _seed(session: AsyncSession, project, prompt, model, gold_gt):
    article = Article(project_id=project.id, pmid="111", title="A", methods_text="Methods text.")
    session.add(article)
    await session.commit()
    session.add(
        GoldArticle(project_id=project.id, article_id=article.id, ground_truth_json=gold_gt)
    )
    await session.commit()


async def test_run_benchmark_completes(engine, session):
    project = Project(title="P", created_by=None)
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
    gt = _gt()
    await _seed(session, project, prompt, model, gt)
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference(gt)
    result = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)

    assert result["models"] == 1
    assert result["articles"] == 1
    assert result["results"][0]["model_name"] == "Kev-4B"
    assert result["results"][0]["f1_score"] == 1.0
    assert fake.calls == 1
    # run passé COMPLETED
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED


async def test_run_benchmark_no_gold_raises(engine, session):
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(LookupError):
        await _run_benchmark(str(run.id), session_factory=factory, inference=FakeInference(_gt()))


async def test_run_benchmark_inference_failure_marks_failed(engine, session, monkeypatch):
    """make_inference_service échoue → run FAILED (pas bloqué en RUNNING)."""
    import app.tasks.benchmark as bench

    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, _gt())
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _fail_inference(db, http=None):
        raise RuntimeError("LLM config missing")

    monkeypatch.setattr(bench, "make_inference_service", _fail_inference)
    with pytest.raises(RuntimeError):
        await _run_benchmark(str(run.id), session_factory=factory)
    await session.refresh(run)
    assert run.status == BenchmarkStatus.FAILED
