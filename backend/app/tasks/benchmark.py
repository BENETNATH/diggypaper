"""Task benchmark multi-modèles (Arena) — Module 3.

Même pattern d'exécution que ``import_pubmed_task`` (C1/C2) : wrapper sync Celery
→ thread dédié → ``asyncio.run`` du core async ; engine par run (ou
``session_factory`` injecté en tests).

Le benchmark exécute les inférences **séquentiellement** sur le Gold Dataset,
pour chaque modèle testé, et calcule les métriques (accuracy, précision, rappel,
F1) en comparant chaque prédiction au ``ground_truth_json``.
"""

import asyncio
import logging
import resource
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.article_text import fill_prompt
from app.celery_app import celery_app
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    BackendType,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    infer_with,
    make_inference_service,
    unload_ollama,
)
from app.inference.onnx_driver import unload as onnx_unload
from app.inference.scoring import is_scorer, score_prediction
from app.schema import compute_metrics

logger = logging.getLogger(__name__)

#: Hook de test : session_factory injecté pour le vrai chemin thread+asyncio.run.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _compute_metrics(prediction: dict, ground_truth: dict) -> dict:
    """Métriques déterministes entre une prédiction et le gold (spec §5).

    - ``accuracy`` : fraction des 3 champs scalaires (``use_ai_behavior``,
      ``animal_sex``, ``confidence_score``) exacts.
    - ``precision_score`` / ``recall_score`` / ``f1_score`` : sur
      ``evidence_quotes`` (comparaison d'ensembles) ; 1.0 si le gold n'a pas de
      quotes (rien à extraire).
    """
    scalar_fields = ("use_ai_behavior", "animal_sex", "confidence_score")
    exact = sum(1 for f in scalar_fields if prediction.get(f) == ground_truth.get(f))
    accuracy = exact / len(scalar_fields)

    pred_quotes = set(prediction.get("evidence_quotes") or [])
    gold_quotes = set(ground_truth.get("evidence_quotes") or [])
    if not gold_quotes:
        precision = recall = f1 = 1.0
    else:
        inter = pred_quotes & gold_quotes
        precision = len(inter) / len(pred_quotes) if pred_quotes else 0.0
        recall = len(inter) / len(gold_quotes)
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "accuracy": accuracy,
        "precision_score": precision,
        "recall_score": recall,
        "f1_score": f1,
    }


@celery_app.task(name="diggypaper.benchmark")
def benchmark_task(benchmark_run_id: str) -> dict:
    """Wrapper sync Celery : exécute le core async dans un thread dédié (C1/C2)."""
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_run_benchmark(benchmark_run_id))
        except BaseException as exc:  # propagée au thread principal
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
    """Core async du benchmark.

    - ``session_factory`` injectable (tests) ; sinon engine par run (C2).
    - ``inference`` injectable (tests) ; sinon ``make_inference_service``.
    - Séquentiel par modèle, puis par gold article.
    - Renvoie ``{"models", "articles", "results"}``.
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
                return {}  # arrêté avant de démarrer, ou redélivré
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


async def _do_benchmark(
    session: AsyncSession, run: BenchmarkRun, inference: OpenAICompatibleService
) -> dict:
    """Corps du benchmark : pour chaque modèle, inférence sur chaque gold article."""
    prompt = await session.get(PromptTemplate, run.prompt_template_id)
    if prompt is None:
        raise LookupError(f"prompt template not found: {run.prompt_template_id}")

    project = await session.get(Project, run.project_id)
    project_sections = project.text_sections if project else None
    project_fields_custom = project.fields if project else None
    gold_rows = (
        await session.execute(
            select(GoldArticle, Article)
            .join(Article, GoldArticle.article_id == Article.id)
            .where(GoldArticle.project_id == run.project_id)
        )
    ).all()
    if not gold_rows:
        raise LookupError(f"no gold articles for project {run.project_id}")

    models = (
        (
            await session.execute(
                select(ModelRegistry).where(ModelRegistry.name.in_(run.models_tested or []))
            )
        )
        .scalars()
        .all()
    )
    if not models:
        raise LookupError(f"no registered models for {run.models_tested}")

    results_payload = []
    total_steps = len(models) * len(gold_rows)
    done_steps = 0
    for model in models:
        total_time = 0.0
        metrics_sum = {
            "accuracy": 0.0,
            "precision_score": 0.0,
            "recall_score": 0.0,
            "f1_score": 0.0,
        }
        is_onnx = is_scorer(model)  # scoreur : métriques de pertinence (pas de champs génératifs)
        tp = fp = fn = correct = (
            0  # encodeur : pertinent prédit vs gold (clé « relevant », déf. True)
        )
        for gold, article in gold_rows:
            if await _cancelled(session, run):
                return {"cancelled": True, "models": len(results_payload)}
            start = time.monotonic()
            if is_onnx:
                score = (await score_prediction(model, project, article, light=False))[
                    "confidence_score"
                ]
                total_time += time.monotonic() - start
                predicted = score >= 0.5
                truth = bool(gold.ground_truth_json.get("relevant", True))
                tp += predicted and truth
                fp += predicted and not truth
                fn += truth and not predicted
                correct += predicted == truth
                done_steps += 1
                run.progress = {"done": done_steps, "total": total_steps, "model": model.name}
                await session.commit()
                ram_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                continue
            user_prompt = fill_prompt(prompt.user_prompt_schema, article, project_sections)
            try:
                prediction = await infer_with(
                    model, inference, prompt.system_prompt or "", user_prompt
                )
            except InferenceError as exc:
                if str(exc) != "invalid_json":
                    raise
                prediction = {}  # sortie non-JSON : comptée comme réponse fausse
            total_time += time.monotonic() - start
            done_steps += 1
            run.progress = {"done": done_steps, "total": total_steps, "model": model.name}
            await session.commit()
            if project_fields_custom is not None:
                metrics = compute_metrics(project_fields_custom, prediction, gold.ground_truth_json)
            else:
                metrics = _compute_metrics(prediction, gold.ground_truth_json)
            for k, v in metrics.items():
                metrics_sum[k] += v
            # RAM pic (ru_maxrss, Ko sur Linux) — empreinte du process.
            ram_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        n = len(gold_rows)
        if is_onnx:
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            avg = {
                "accuracy": correct / n,
                "precision_score": prec,
                "recall_score": rec,
                "f1_score": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
            }
            if model.backend_type == BackendType.ONNX_LOCAL:
                onnx_unload(model.model_identifier)
        else:
            avg = {k: v / n for k, v in metrics_sum.items()}
            base = getattr(inference, "base_url", None)
            if model.backend_type == BackendType.OLLAMA and isinstance(base, str):
                await unload_ollama(base, model.model_identifier)  # libère la RAM avant le suivant
        result = BenchmarkResult(
            benchmark_run_id=run.id,
            model_name=model.name,
            execution_time_seconds=total_time,
            accuracy=avg["accuracy"],
            precision_score=avg["precision_score"],
            recall_score=avg["recall_score"],
            f1_score=avg["f1_score"],
        )
        session.add(result)
        results_payload.append(
            {
                "model_name": model.name,
                "execution_time_seconds": total_time,
                "accuracy": avg["accuracy"],
                "precision_score": avg["precision_score"],
                "recall_score": avg["recall_score"],
                "f1_score": avg["f1_score"],
                "ram_usage_mb": ram_mb,
            }
        )
        await session.commit()

    run.status = BenchmarkStatus.COMPLETED
    await session.commit()
    return {
        "models": len(models),
        "articles": len(gold_rows),
        "results": results_payload,
    }


async def _cancelled(session: AsyncSession, run: BenchmarkRun) -> bool:
    """Arrêt demandé depuis l'interface (POST …/benchmarks/{id}/cancel : statut FAILED en base)."""
    status = (
        await session.execute(select(BenchmarkRun.status).where(BenchmarkRun.id == run.id))
    ).scalar_one_or_none()
    return status == BenchmarkStatus.FAILED


async def _record_failure(session: AsyncSession, run: BenchmarkRun, exc: Exception) -> None:
    """Échec : run FAILED (best effort)."""
    logger.error("benchmark failed (run %s): %s", run.id, _sanitize(exc))
    try:
        await session.rollback()
        run.status = BenchmarkStatus.FAILED
        await session.commit()
    except Exception:
        logger.exception("failed to record benchmark failure")


def _sanitize(exc: BaseException) -> str:
    """Message court (pas de traceback brut)."""
    return str(exc)[:500] or type(exc).__name__
