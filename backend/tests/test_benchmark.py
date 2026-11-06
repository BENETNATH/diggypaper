"""Tests for the benchmark task (app/tasks/benchmark.py) — 100% offline."""

import pytest
from conftest import LEGACY_FIELDS
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
    """Mocked inference: returns a fixed prediction."""

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
    # run moved to COMPLETED
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED


@pytest.mark.parametrize(("raw", "stored"), [("high", None), ("0.9", 0.9), (85, None)])
async def test_run_benchmark_non_numeric_confidence_completes(engine, session, raw, stored):
    """textual / out-of-[0, 1] confidence_score: the run finishes, Float column = None / float."""
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
    """make_inference_service fails → run FAILED (not stuck in RUNNING)."""
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
    """Remembers the system prompts received (variants, gold examples)."""

    def __init__(self, prediction: dict):
        super().__init__(prediction)
        self.systems: list[str] = []

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.systems.append(system_prompt)
        return await super().infer(model, system_prompt, user_prompt)


async def test_benchmark_provider_error_recorded_not_fatal(engine, session):
    """Provider error echoed in the content (HTTP 200): the run completes and the
    prediction stores the error, not the error JSON as a decision."""
    from app.inference import InferenceError

    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Text: {methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    good = Article(project_id=project.id, pmid="1", title="G", methods_text="GOOD")
    bad = Article(project_id=project.id, pmid="2", title="B", methods_text="BADMARKER")
    session.add_all([good, bad])
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class Flaky:
        async def infer(self, m, system, user):
            if "BADMARKER" in user:
                raise InferenceError("provider_error_404")
            return _gt()

    await _run_benchmark(str(run.id), session_factory=factory, inference=Flaky())
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED
    row = (
        await session.execute(
            select(BenchmarkPrediction).where(
                BenchmarkPrediction.benchmark_run_id == run.id,
                BenchmarkPrediction.article_id == bad.id,
            )
        )
    ).scalar_one()
    assert row.error == "provider_error_404"
    assert not row.decision_json
    # the error count is exposed in the variant's results
    from app.db.models import BenchmarkResult

    (res,) = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert res.details["errors"] == 1


async def test_benchmark_retry_errors(engine, session):
    """Retry of the failed predictions only: the error is recomputed in place, the
    run stays COMPLETED and the results (error count) are recomputed."""
    from app.inference import InferenceError
    from app.tasks.benchmark import _retry_benchmark_errors

    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Text: {methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    good = Article(project_id=project.id, pmid="1", title="G", methods_text="GOOD")
    bad = Article(project_id=project.id, pmid="2", title="B", methods_text="BADMARKER")
    session.add_all([good, bad])
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class Flaky:
        async def infer(self, m, system, user):
            if "BADMARKER" in user:
                raise InferenceError("provider_error_404")
            return _gt()

    await _run_benchmark(str(run.id), session_factory=factory, inference=Flaky())

    fixed = FakeInference(_gt())
    out = await _retry_benchmark_errors(str(run.id), session_factory=factory, inference=fixed)
    assert out == {"retried": 1, "still_failing": 0}
    assert fixed.calls == 1  # only the failed prediction was re-run
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED
    row = (
        await session.execute(
            select(BenchmarkPrediction).where(
                BenchmarkPrediction.benchmark_run_id == run.id,
                BenchmarkPrediction.article_id == bad.id,
            )
        )
    ).scalar_one()
    assert row.error is None
    assert row.decision_json
    from app.db.models import BenchmarkResult

    (res,) = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert res.details["errors"] == 0


async def test_benchmark_retry_errors_persistent_failure(engine, session):
    """The provider keeps failing: the prediction keeps the new error code, the run
    stays COMPLETED, the error count stays >= 1."""
    from app.inference import InferenceError
    from app.tasks.benchmark import _retry_benchmark_errors

    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Text: {methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    bad = Article(project_id=project.id, pmid="1", title="B", methods_text="BADMARKER")
    session.add(bad)
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class AlwaysBroken:
        async def infer(self, m, system, user):
            raise InferenceError("provider_error_FAILED_PRECONDITION")

    await _run_benchmark(str(run.id), session_factory=factory, inference=AlwaysBroken())
    out = await _retry_benchmark_errors(
        str(run.id), session_factory=factory, inference=AlwaysBroken()
    )
    assert out == {"retried": 1, "still_failing": 1}
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED
    row = (
        await session.execute(
            select(BenchmarkPrediction).where(BenchmarkPrediction.benchmark_run_id == run.id)
        )
    ).scalar_one()
    assert row.error == "provider_error_FAILED_PRECONDITION"
    assert not row.decision_json
    from app.db.models import BenchmarkResult

    (res,) = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert res.details["errors"] == 1


async def _project_with_articles(session, n=4, fields=None):
    project = Project(title="P", created_by=None, fields=fields)
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
    project, prompt, arts = await _project_with_articles(session, 3, fields=LEGACY_FIELDS)
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
    systems = fake.systems
    assert any("Be strict." in s for s in systems) and any("Be free." in s for s in systems)
    # each variant's question is wrapped in the project's field schema (structured)
    assert all("use_ai_behavior" in s and "confidence_score" in s for s in systems)
    rows = (await session.execute(select(BenchmarkPrediction))).scalars().all()
    assert len(rows) == 6 and {r.variant for r in rows} == {"strict", "libre"}
    out = {(r["variant"]): r for r in res["results"]}
    assert (
        set(out) == {"strict", "libre"} and out["strict"]["f1_score"] is None
    )  # no gold requested
    # agreement between variants measured without a reference
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


def test_benchmark_imports_without_resource(monkeypatch):
    """The Unix ``resource`` module is absent on Windows: the task must still import
    and return ``None`` for the RAM."""
    import importlib
    import sys

    import app.tasks.benchmark as benchmark

    # ``None`` in sys.modules makes ``import resource`` raise ImportError.
    monkeypatch.setitem(sys.modules, "resource", None)
    try:
        importlib.reload(benchmark)
        assert benchmark.resource is None
        assert benchmark._peak_ram_mb() is None
    finally:
        monkeypatch.undo()
        importlib.reload(benchmark)  # restaure le comportement natif de la plateforme


async def test_benchmark_uses_structured_schema(engine, session):
    """2026-11-03: the benchmark wraps each variant's question in the project's
    field schema (structured JSON, like the complete analysis) instead of a bare
    free-form prompt — so the responses have a constant, comparable format."""
    project, prompt, arts = await _project_with_articles(session, 1, fields=LEGACY_FIELDS)
    prompt.system_prompt = "Describe the forced swim test protocol"
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},  # no gold
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = RecordingInference(_gt())
    await _run_benchmark(str(run.id), session_factory=factory, inference=fake)
    (system,) = fake.systems
    # the variant's question is embedded
    assert "Describe the forced swim test protocol" in system
    # the project's field schema is enforced (structured JSON, not free text)
    assert "use_ai_behavior" in system
    assert "animal_sex" in system
    assert "confidence_score" in system
    assert "evidence_quotes" in system
    assert "Return ONLY a valid JSON object" in system
