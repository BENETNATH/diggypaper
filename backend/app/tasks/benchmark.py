"""Benchmark task (tuning): compare variants (model × prompt) on a lot of articles.

Same execution pattern as ``import_pubmed_task``: sync Celery wrapper → dedicated thread →
``asyncio.run`` of the async core; engine per run (or ``session_factory`` injected in tests).

The test bench is a search's lot. Each variant answers each article; the answers are
kept (``benchmark_predictions``) for side-by-side comparison. If the gold is requested, the
variants are scored against it (lot articles present in the gold, excluding prompt examples);
otherwise their agreement is measured. Prompt variants concern generative models only.
"""

import asyncio
import logging
import threading
import time
import uuid

try:
    import resource  # Unix seulement : absent sous Windows
except ImportError:  # pragma: no cover - platform-dependent
    resource = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import metrics
from app.article_text import fill_prompt
from app.celery_app import celery_app
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    BackendType,
    BenchmarkPrediction,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.gold_examples import examples_block, pick_examples
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    infer_with,
    make_inference_service,
    unload_ollama,
)
from app.schema import project_fields
from app.searches import ScopedProject, get_search, gold_condition, lot_condition

logger = logging.getLogger(__name__)

#: Test hook: injected session_factory for the real thread+asyncio.run path.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

DEFAULT_USER_PROMPT = "Article text:\n{article_text}"


def _peak_ram_mb() -> float | None:
    """RAM max du processus (Mo) via ``resource`` ; ``None`` sans ce module (Windows)."""
    if resource is None:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="diggypaper.benchmark")
def benchmark_task(benchmark_run_id: str) -> dict:
    """Sync Celery wrapper: runs the async core in a dedicated thread (C1/C2)."""
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_run_benchmark(benchmark_run_id))
        except BaseException as exc:  # propagated to the main thread
            error = exc

    thread = threading.Thread(target=_run, name="benchmark")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


async def _run_benchmark(
    benchmark_run_id: str,
    session_factory=None,
    http=None,
    inference: OpenAICompatibleService | None = None,
) -> dict:
    """Async core of the benchmark.

    - ``session_factory`` injectable (tests); else engine per run (C2).
    - ``inference`` injectable (tests); else ``make_inference_service``.
    - Sequential per model, then per gold article.
    - Returns ``{"models", "articles", "results"}``.
    """
    if session_factory is None:
        session_factory = _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    client = http if http is not None else None
    try:
        try:
            run_uuid = uuid.UUID(benchmark_run_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid benchmark run id: {benchmark_run_id}") from exc

        async with factory() as session:
            run = await session.get(BenchmarkRun, run_uuid)
            if run is None:
                raise LookupError(f"benchmark run not found: {benchmark_run_id}")
            if run.status in (BenchmarkStatus.FAILED, BenchmarkStatus.COMPLETED):
                return {}  # stopped before starting, or redelivered
            run.status = BenchmarkStatus.RUNNING
            await session.commit()

            try:
                if inference is None:
                    inference = await make_inference_service(session, http=client)
                return await _do_benchmark(session, run, inference)
            except Exception as exc:
                await _record_failure(session, run, exc)
                raise
    finally:
        if engine is not None:
            await engine.dispose()


async def _references(
    session: AsyncSession,
    project: ScopedProject,
    base_project: Project,
    search,
    lot_ids: set,
    opts: dict,
) -> tuple[dict, list, str]:
    """References shared by the run and the results recomputation:
    ``gold_all`` (validation + examples), ``example_ids``, ``shots`` (examples block)."""
    gold_all: dict = {}
    if opts.get("use_gold") or opts.get("gold_examples"):
        rows = await session.execute(
            select(GoldArticle).where(await gold_condition(session, base_project, search))
        )
        gold_all = {g.article_id: g.ground_truth_json for g in rows.scalars() if g.article_id}
    example_ids: list = []
    if opts.get("gold_examples") and gold_all:
        outside = {i: g for i, g in gold_all.items() if i not in lot_ids}
        pool = outside if len(outside) >= opts["gold_examples"] else gold_all
        example_ids = pick_examples(pool, int(opts["gold_examples"]), seed=1)
    shots = ""
    if example_ids:
        ex_articles = {
            a.id: a
            for a in (
                await session.execute(select(Article).where(Article.id.in_(example_ids)))
            ).scalars()
        }
        shots = examples_block(
            ex_articles, gold_all, example_ids, project_fields(project), project.text_sections
        )
    return gold_all, example_ids, shots


async def _recompute_results(session: AsyncSession, run: BenchmarkRun) -> None:
    """Recomputes the per-variant results (scores, agreement, error count) from the
    stored predictions — used after the retry of the failed predictions."""
    base_project = await session.get(Project, run.project_id)
    search = await get_search(session, base_project, run.search_id)
    project = ScopedProject(base_project, search)
    fields = project_fields(project)
    lot_ids = set(
        (await session.execute(select(Article.id).where(lot_condition(search)))).scalars().all()
    )
    opts = run.options or {}
    gold_all, example_ids, _shots = await _references(
        session, project, base_project, search, lot_ids, opts
    )
    gold = {i: g for i, g in gold_all.items() if i in lot_ids} if opts.get("use_gold") else {}
    preds_rows = (
        (
            await session.execute(
                select(BenchmarkPrediction).where(BenchmarkPrediction.benchmark_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    # predictions by (model, variant): the prediction always stores the variant name;
    # the result row stores None when the run had a single variant.
    by_model: dict[str, dict[str, dict]] = {}
    err_counts: dict[tuple, int] = {}
    for p in preds_rows:
        v = p.variant or ""
        by_model.setdefault(p.model_name, {}).setdefault(v, {})[p.article_id] = p.decision_json
        if p.error:
            err_counts[(p.model_name, v)] = err_counts.get((p.model_name, v), 0) + 1
    results = (
        (
            await session.execute(
                select(BenchmarkResult).where(BenchmarkResult.benchmark_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    for res in results:
        model_groups = by_model.get(res.model_name, {})
        if res.variant in model_groups:
            vkey = res.variant
        elif len(model_groups) == 1:  # single-variant run: the result row variant is None
            vkey = next(iter(model_groups))
        else:
            vkey = res.variant or ""
        preds = model_groups.get(vkey, {})
        key = (res.model_name, vkey)
        details: dict = {"articles": len(preds), "errors": err_counts.get(key, 0)}
        if gold:
            scored = metrics.score(fields, preds, gold, exclude=set(example_ids))
            target, metric, value = metrics.primary_value(scored, fields)
            rel = scored["relevance"]
            res.n_scored = scored["n"]
            res.accuracy = scored["accuracy"] if scored["accuracy"] is not None else rel["accuracy"]
            res.precision_score, res.recall_score, res.f1_score = (
                rel["precision"],
                rel["recall"],
                rel["f1"],
            )
            details.update(
                scored=scored, primary={"item": target, "metric": metric, "value": value}
            )
        if len(model_groups) > 1:  # agreement between variants (no reference)
            agree = metrics.agreement(model_groups, fields)
            details["agreement"] = agree.get(vkey)
        res.details = details
    await session.commit()


async def _retry_benchmark_errors(
    run_id: str,
    session_factory=None,
    inference: OpenAICompatibleService | None = None,
) -> dict:
    """Re-runs, in place, only the failed predictions of a completed benchmark
    (provider errors, invalid JSON), then recomputes the variant results.

    Returns ``{"retried": n, "still_failing": m}``."""
    if session_factory is None:
        session_factory = _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    try:
        try:
            run_uuid = uuid.UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid benchmark run id: {run_id}") from exc
        async with factory() as session:
            run = await session.get(BenchmarkRun, run_uuid)
            if run is None:
                raise LookupError(f"benchmark run not found: {run_id}")
            if run.status != BenchmarkStatus.COMPLETED:
                raise LookupError("only a completed benchmark can be retried")
            failed = (
                (
                    await session.execute(
                        select(BenchmarkPrediction).where(
                            BenchmarkPrediction.benchmark_run_id == run.id,
                            BenchmarkPrediction.error.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not failed:
                return {"retried": 0, "still_failing": 0}
            base_project = await session.get(Project, run.project_id)
            search = await get_search(session, base_project, run.search_id)
            project = ScopedProject(base_project, search)
            lot_ids = set(
                (await session.execute(select(Article.id).where(lot_condition(search))))
                .scalars()
                .all()
            )
            opts = run.options or {}
            _gold_all, _example_ids, shots = await _references(
                session, project, base_project, search, lot_ids, opts
            )
            variants = await _prompts(session, run)
            vmap = {name: (system, user) for name, system, user in variants}
            names = {p.model_name for p in failed}
            models = {
                m.name: m
                for m in (
                    await session.execute(
                        select(ModelRegistry).where(ModelRegistry.name.in_(names))
                    )
                )
                .scalars()
                .all()
            }
            articles = {
                a.id: a
                for a in (
                    await session.execute(
                        select(Article).where(Article.id.in_({p.article_id for p in failed}))
                    )
                )
                .scalars()
                .all()
            }
            if inference is None:
                inference = await make_inference_service(session)
            still_failing = 0
            for p in failed:
                model = models.get(p.model_name)
                article = articles.get(p.article_id)
                if model is None or article is None or p.variant not in vmap:
                    still_failing += 1
                    continue
                system, user_schema = vmap[p.variant]
                start = time.monotonic()
                try:
                    prediction = await infer_with(
                        model,
                        inference,
                        (system + "\n\n" + shots).strip() if shots else system,
                        fill_prompt(user_schema, article, project.text_sections),
                    )
                    if not isinstance(prediction, dict):
                        raise InferenceError("invalid_json")
                    error = None
                except InferenceError as exc:
                    code = str(exc)
                    if code != "invalid_json" and not code.startswith("provider_error_"):
                        raise
                    prediction, error = {}, code
                took = time.monotonic() - start
                p.decision_json = prediction
                p.confidence_score = metrics.confidence(prediction.get("confidence_score"))
                p.error = error
                p.seconds = took
                if error:
                    still_failing += 1
                await session.commit()
            await _recompute_results(session, run)
            return {"retried": len(failed), "still_failing": still_failing}
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(name="diggypaper.benchmark.retry_errors")
def retry_benchmark_errors_task(benchmark_run_id: str) -> dict:
    """Sync Celery wrapper: retry of the failed predictions of a benchmark (C1/C2)."""
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_retry_benchmark_errors(benchmark_run_id))
        except BaseException as exc:  # propagated to the main thread
            error = exc

    thread = threading.Thread(target=_run, name="benchmark-retry")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


async def _prompts(session: AsyncSession, run: BenchmarkRun) -> list[tuple[str, str, str]]:
    """Resolved variants: [(name, system prompt, user prompt schema)]."""
    out = []
    for v in run.variants or [{}]:
        pid = v.get("prompt_template_id") or (
            None if v.get("system_prompt") else run.prompt_template_id
        )
        if pid:
            pt = await session.get(PromptTemplate, uuid.UUID(str(pid)))
            if pt is None:
                raise LookupError(f"prompt template not found: {pid}")
            system, user = pt.system_prompt or "", pt.user_prompt_schema or DEFAULT_USER_PROMPT
            name = v.get("name") or pt.title or pt.version_number
        else:
            system = v.get("system_prompt") or ""
            user = v.get("user_prompt_schema") or DEFAULT_USER_PROMPT
            name = v.get("name") or f"variante {len(out) + 1}"
        out.append((name, system, user))
    return out


async def _do_benchmark(
    session: AsyncSession, run: BenchmarkRun, inference: OpenAICompatibleService
) -> dict:
    """Benchmark body: each (model, variant) answers each article of the lot."""
    base_project = await session.get(Project, run.project_id)
    search = await get_search(session, base_project, run.search_id)
    project = ScopedProject(base_project, search)  # question / items / parts of the search
    fields = project_fields(project)
    lot = (
        (
            await session.execute(
                select(Article)
                .where(lot_condition(search))
                .order_by(Article.created_at, Article.id)
            )
        )
        .scalars()
        .all()
    )
    if not lot:
        raise LookupError(f"no articles in the benchmark lot of search {search.id}")
    lot_ids = {a.id for a in lot}
    opts = run.options or {}

    # References: prompt examples (outside the lot when possible) and validation notes
    gold_all, example_ids, shots = await _references(
        session, project, base_project, search, lot_ids, opts
    )
    gold = {i: g for i, g in gold_all.items() if i in lot_ids} if opts.get("use_gold") else {}
    if opts.get("use_gold") and not (set(gold) - set(example_ids)):
        raise LookupError("no gold articles in the benchmark lot")

    variants = await _prompts(session, run)
    names = run.models_tested or []
    found = {
        m.name: m
        for m in (await session.execute(select(ModelRegistry).where(ModelRegistry.name.in_(names))))
        .scalars()
        .all()
    }
    models = [found[n] for n in names if n in found]
    if not models:
        raise LookupError(f"no registered models for {run.models_tested}")

    total_steps = len(lot) * len(variants) * len(models)
    done_steps = 0
    results_payload = []
    ram_mb = 0.0
    for model in models:
        todo = variants
        by_variant: dict[str, dict] = {}
        pending: list[BenchmarkResult] = []
        for vname, system, user_schema in todo:
            preds: dict = {}
            total_time = 0.0
            errors = 0
            for article in lot:
                if await _cancelled(session, run):
                    return {"cancelled": True, "models": len(results_payload)}
                start = time.monotonic()
                error = None
                try:
                    prediction = await infer_with(
                        model,
                        inference,
                        (system + "\n\n" + shots).strip() if shots else system,
                        fill_prompt(user_schema, article, project.text_sections),
                    )
                    if not isinstance(prediction, dict):
                        raise InferenceError("invalid_json")  # valid JSON but not an object
                except InferenceError as exc:
                    code = str(exc)
                    # Per-article, non fatal: non-JSON output (invalid_json) or an
                    # upstream provider error echoed in the content (provider_error_*).
                    if code != "invalid_json" and not code.startswith("provider_error_"):
                        raise
                    prediction, error = {}, code
                    errors += 1
                took = time.monotonic() - start
                total_time += took
                preds[article.id] = prediction
                session.add(
                    BenchmarkPrediction(
                        benchmark_run_id=run.id,
                        article_id=article.id,
                        model_name=model.name,
                        variant=vname,
                        decision_json=prediction,
                        confidence_score=metrics.confidence(prediction.get("confidence_score")),
                        error=error,
                        seconds=took,
                    )
                )
                done_steps += 1
                run.progress = {
                    "done": done_steps,
                    "total": total_steps,
                    "model": model.name,
                    "variant": vname,
                }
                await session.commit()
                ram_mb = _peak_ram_mb()
            by_variant[vname] = preds
            res = BenchmarkResult(
                benchmark_run_id=run.id,
                model_name=model.name,
                variant=vname if len(variants) > 1 else None,
                execution_time_seconds=total_time,
            )
            details: dict = {"articles": len(lot), "errors": errors}
            if gold:
                scored = metrics.score(fields, preds, gold, exclude=set(example_ids))
                target, metric, value = metrics.primary_value(scored, fields)
                rel = scored["relevance"]
                res.n_scored = scored["n"]
                res.accuracy = (
                    scored["accuracy"] if scored["accuracy"] is not None else rel["accuracy"]
                )
                res.precision_score, res.recall_score, res.f1_score = (
                    rel["precision"],
                    rel["recall"],
                    rel["f1"],
                )
                details.update(
                    scored=scored, primary={"item": target, "metric": metric, "value": value}
                )
            res.details = details
            pending.append(res)
        if len(by_variant) > 1:  # agreement between variants (no reference)
            agree = metrics.agreement(by_variant, fields)
            for r in pending:
                r.details = {**(r.details or {}), "agreement": agree.get(r.variant)}
        for r in pending:
            session.add(r)
            results_payload.append(
                {
                    "model_name": r.model_name,
                    "variant": r.variant,
                    "execution_time_seconds": r.execution_time_seconds,
                    "accuracy": r.accuracy,
                    "precision_score": r.precision_score,
                    "recall_score": r.recall_score,
                    "f1_score": r.f1_score,
                    "ram_usage_mb": ram_mb,
                }
            )
        base = getattr(inference, "base_url", None)
        if model.backend_type == BackendType.OLLAMA and isinstance(base, str):
            await unload_ollama(base, model.model_identifier)  # frees RAM before the next one
        await session.commit()

    run.status = BenchmarkStatus.COMPLETED
    await session.commit()
    return {
        "models": len(models),
        "articles": len(lot),
        "variants": len(variants),
        "results": results_payload,
    }


async def _cancelled(session: AsyncSession, run: BenchmarkRun) -> bool:
    """Stop requested from the UI (POST …/benchmarks/{id}/cancel: FAILED status in the database)."""
    status = (
        await session.execute(select(BenchmarkRun.status).where(BenchmarkRun.id == run.id))
    ).scalar_one_or_none()
    return status == BenchmarkStatus.FAILED


async def _record_failure(session: AsyncSession, run: BenchmarkRun, exc: Exception) -> None:
    """Failure: run FAILED (best effort)."""
    logger.error("benchmark failed (run %s): %s", run.id, _sanitize(exc))
    try:
        await session.rollback()
        run.status = BenchmarkStatus.FAILED
        await session.commit()
    except Exception:
        logger.exception("failed to record benchmark failure")


def _sanitize(exc: BaseException) -> str:
    """Short message (no raw traceback)."""
    return str(exc)[:500] or type(exc).__name__
