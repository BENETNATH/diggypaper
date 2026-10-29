"""Tests de la task benchmark (app/tasks/benchmark.py) — 100 % offline."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    BenchmarkPrediction,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.tasks.benchmark import _run_benchmark


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
        options={"use_gold": True},
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


@pytest.mark.parametrize(("raw", "stored"), [("high", None), ("0.9", 0.9), (85, None)])
async def test_run_benchmark_non_numeric_confidence_completes(engine, session, raw, stored):
    """confidence_score textuel / hors [0, 1] : le run se termine, colonne Float = None / float."""
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
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, _gt())
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_benchmark(
        str(run.id), session_factory=factory, inference=FakeInference(_gt(conf=raw))
    )
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED
    pred = (await session.execute(select(BenchmarkPrediction))).scalars().one()
    assert pred.confidence_score == stored


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
        options={"use_gold": True},
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
        options={"use_gold": True},
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


class RecordingInference(FakeInference):
    """Mémorise les prompts système reçus (variantes, exemples du gold)."""

    def __init__(self, prediction: dict):
        super().__init__(prediction)
        self.systems: list[str] = []

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.systems.append(system_prompt)
        return await super().infer(model, system_prompt, user_prompt)


async def _project_with_articles(session, n=4):
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id, version_number="v1.0", system_prompt="AUTO", user_prompt_schema=None
    )
    session.add_all([prompt, ModelRegistry(name="Kev-4B", model_identifier="x")])
    arts = [
        Article(project_id=project.id, pmid=str(i), title=f"A{i}", abstract=f"abstract {i}")
        for i in range(n)
    ]
    session.add_all(arts)
    await session.commit()
    return project, prompt, arts


async def test_benchmark_variants_store_predictions_and_agreement(engine, session):
    project, prompt, arts = await _project_with_articles(session, 3)
    run = BenchmarkRun(
        project_id=project.id,
        models_tested=["Kev-4B"],
        variants=[
            {"name": "strict", "system_prompt": "Be strict."},
            {"name": "libre", "system_prompt": "Be free."},
        ],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = RecordingInference(_gt())
    res = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)
    assert res["variants"] == 2 and fake.calls == 6  # 3 articles × 2 variantes
    assert sorted(set(fake.systems)) == ["Be free.", "Be strict."]
    rows = (await session.execute(select(BenchmarkPrediction))).scalars().all()
    assert len(rows) == 6 and {r.variant for r in rows} == {"strict", "libre"}
    out = {(r["variant"]): r for r in res["results"]}
    assert (
        set(out) == {"strict", "libre"} and out["strict"]["f1_score"] is None
    )  # pas de gold demandé
    # accord entre variantes mesuré sans référence
    from app.db.models import BenchmarkResult

    results = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert all(r.details["agreement"] == 1.0 for r in results)


async def test_benchmark_gold_examples_in_prompt_and_excluded_from_scoring(engine, session):
    project, prompt, arts = await _project_with_articles(session, 4)
    for a in arts:  # les 4 articles sont au gold ; 2 serviront d'exemples (hors validation)
        session.add(GoldArticle(project_id=project.id, article_id=a.id, ground_truth_json=_gt()))
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True, "gold_examples": 2},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = RecordingInference(_gt())
    res = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)
    assert all("verified answers" in s for s in fake.systems)  # exemples dans le prompt
    from app.db.models import BenchmarkResult

    (r,) = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert r.n_scored == 2 and res["results"][0]["f1_score"] == 1.0  # 4 gold − 2 exemples
