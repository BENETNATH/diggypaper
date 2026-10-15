"""Task inférence massive (MODULE 4, lot 5).

Même pattern d'exécution que ``benchmark_task`` / ``import_pubmed_task`` (C1/C2) :
wrapper sync Celery → thread dédié → ``asyncio.run`` du core async ; engine par run
(ou ``session_factory`` injecté en tests).

L'inférence exécute le prompt validé sur **chaque article** du corpus (FROZEN),
stocke le résultat dans ``article_predictions``, et applique le **triage
automatique** selon le score de confiance (spec MODULE 4) :

- ``confidence_score > 0.85`` → ``AUTO_ACCEPTED``
- ``confidence_score < 0.15`` → ``AUTO_REJECTED``
- sinon (ou score absent) → ``NEEDS_HUMAN_REVIEW``

À l'achèvement, le job passe ``COMPLETED`` (statistiques dans ``result_json``) et
une notification est envoyée (e-mail best-effort via ``notify_job_complete``).
"""

import asyncio
import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.article_text import fill_prompt
from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
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
from app.emails import Mailer
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    infer_with,
    make_inference_service,
)
from app.inference.scoring import is_scorer, score_prediction
from app.settings.service import get_setting
from app.tasks.notify import notify_job_complete

#: Clés SMTP lues en DB pour ``Mailer.from_settings`` (DB > env).
_SMTP_SETTING_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "use_tls",
)

logger = logging.getLogger(__name__)

#: Hook de test : session_factory injecté pour le vrai chemin thread+asyncio.run.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

#: Bornes de triage (spec MODULE 4).
_AUTO_ACCEPT_THRESHOLD = 0.85
_AUTO_REJECT_THRESHOLD = 0.15


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


REJECTED = PredictionStatus.AUTO_REJECTED


def _triage(confidence_score: float | None) -> PredictionStatus:
    """Triage automatique selon le score de confiance (spec MODULE 4).

    - ``> 0.85`` → ``AUTO_ACCEPTED``
    - ``< 0.15`` → ``AUTO_REJECTED``
    - sinon (bornes incluses, ou score absent) → ``NEEDS_HUMAN_REVIEW``
    """
    if confidence_score is None:
        return PredictionStatus.NEEDS_HUMAN_REVIEW
    if confidence_score > _AUTO_ACCEPT_THRESHOLD:
        return PredictionStatus.AUTO_ACCEPTED
    if confidence_score < _AUTO_REJECT_THRESHOLD:
        return PredictionStatus.AUTO_REJECTED
    return PredictionStatus.NEEDS_HUMAN_REVIEW


@celery_app.task(name="diggypaper.inference")
def inference_task(inference_job_id: str) -> dict:
    """Wrapper sync Celery : exécute le core async dans un thread dédié (C1/C2)."""
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_run_inference(inference_job_id))
        except BaseException as exc:  # propagée au thread principal
            error = exc

    thread = threading.Thread(target=_run, name="inference")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


async def _run_inference(
    inference_job_id: str,
    session_factory=None,
    http=None,
    inference: OpenAICompatibleService | None = None,
) -> dict:
    """Core async de l'inférence massive.

    - ``session_factory`` injectable (tests) ; sinon engine par run (C2).
    - ``inference`` injectable (tests) ; sinon ``make_inference_service``.
    - Séquentiel par article (CPU-friendly, spec).
    - Renvoie les statistiques clés (``processed``, ``auto_accepted``,
      ``auto_rejected``, ``needs_review``, ``duration_seconds``).
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
            job_uuid = uuid.UUID(inference_job_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid inference job id: {inference_job_id}") from exc

        async with factory() as session:
            job = await session.get(InferenceJob, job_uuid)
            if job is None:
                raise LookupError(f"inference job not found: {inference_job_id}")
            if job.status in (InferenceJobStatus.FAILED, InferenceJobStatus.COMPLETED):
                return {}  # tâche redélivrée après un redémarrage : job déjà clos
            job.status = InferenceJobStatus.RUNNING
            await session.commit()

            try:
                if inference is None:
                    inference = await make_inference_service(session, http=client)
                stats = await _do_inference(session, job, inference)
            except Exception as exc:
                await _record_failure(session, job, exc)
                raise

            # Notification e-mail (best-effort, ne casse pas le job).
            mailer = await _make_mailer(session)
            await notify_job_complete(
                str(job.project_id),
                "inference",
                stats,
                session_factory=factory,
                mailer=mailer,
            )
            return stats
    finally:
        if engine is not None:
            await engine.dispose()


async def _do_inference(
    session: AsyncSession, job: InferenceJob, inference: OpenAICompatibleService
) -> dict:
    """Corps de l'inférence : pour chaque article, inférence + triage + persistance."""
    prompt = await session.get(PromptTemplate, job.prompt_template_id)
    if prompt is None:
        raise LookupError(f"prompt template not found: {job.prompt_template_id}")

    project = await session.get(Project, job.project_id)
    if project is None:
        raise LookupError(f"project not found: {job.project_id}")

    articles = (
        (await session.execute(select(Article).where(Article.project_id == project.id))).scalars()
    ).all()

    # Reprise : articles déjà prédits (même prompt) depuis la date du job interrompu.
    done_status: dict = {}
    if job.resume_after is not None:
        rows = await session.execute(
            select(ArticlePrediction.article_id, ArticlePrediction.status).where(
                ArticlePrediction.prompt_template_id == prompt.id,
                ArticlePrediction.created_at >= job.resume_after,
                ArticlePrediction.article_id.in_([a.id for a in articles]),
            )
        )
        done_status = dict(rows.all())

    start = time.monotonic()
    auto_accepted = 0
    auto_rejected = 0
    needs_review = 0
    processed = 0
    invalid = 0

    def counts() -> dict:
        return {
            "auto_accepted": auto_accepted,
            "auto_rejected": auto_rejected,
            "needs_review": needs_review,
        }

    # Modèles du registre : étage 1 (scoreur rapide, optionnel) et modèle principal.
    names = [n for n in (job.model_used, job.prefilter_model) if n]
    registry = {
        m.name: m
        for m in (await session.execute(select(ModelRegistry).where(ModelRegistry.name.in_(names))))
        .scalars()
        .all()
    }
    pre = registry.get(job.prefilter_model) if job.prefilter_model else None
    main = registry.get(job.model_used)
    prefiltered = 0
    # Visible tout de suite (0 / N) : le premier appel peut télécharger les poids du modèle.
    await _progress(session, job, 0, len(articles), 0, counts())

    async def predict(model, article, *, light: bool) -> dict:
        """Scoreur (Laya…) ou LLM génératif ; ``light`` = étage 1 (abstract seulement)."""
        if is_scorer(model):
            return await score_prediction(model, project, article, light=light)
        kinds = ["abstract"] if light else project.text_sections
        user_prompt = fill_prompt(prompt.user_prompt_schema, article, kinds)
        return await infer_with(
            model, inference, prompt.system_prompt or "", user_prompt, name=job.model_used
        )

    for article in articles:
        if article.id in done_status:  # déjà fait avant l'interruption : on le compte, on saute
            st = done_status[article.id]
            auto_accepted += st == PredictionStatus.AUTO_ACCEPTED
            auto_rejected += st == REJECTED
            needs_review += st == PredictionStatus.NEEDS_HUMAN_REVIEW
            processed += 1
            continue
        model_name = job.model_used
        prediction = None
        try:
            if pre is not None:
                # Étage 1 : titre + abstract ; un rejet net écarte l'article sans étage 2.
                p1 = await predict(pre, article, light=True)
                if _triage(p1.get("confidence_score")) == REJECTED:
                    prediction, model_name = p1, pre.name
                    prefiltered += 1
            if prediction is None:
                prediction = await predict(main, article, light=False)
        except InferenceError as exc:
            # Petits modèles : une sortie non-JSON ne doit pas tuer tout le job ;
            # timeout / connexion / HTTP restent fatals.
            if str(exc) != "invalid_json":
                raise
            invalid += 1
            await _progress(session, job, processed, len(articles), invalid, counts())
            continue
        confidence = prediction.get("confidence_score")
        status = _triage(confidence)
        if status == PredictionStatus.AUTO_ACCEPTED:
            auto_accepted += 1
        elif status == PredictionStatus.AUTO_REJECTED:
            auto_rejected += 1
        else:
            needs_review += 1
        processed += 1
        session.add(
            ArticlePrediction(
                article_id=article.id,
                prompt_template_id=prompt.id,
                model_used=model_name,
                decision_json=prediction,
                confidence_score=confidence,
                evidence_quotes=prediction.get("evidence_quotes"),
                status=status,
            )
        )
        await _progress(session, job, processed, len(articles), invalid, counts())

    duration = time.monotonic() - start
    stats = {
        "processed": processed,
        "auto_accepted": auto_accepted,
        "auto_rejected": auto_rejected,
        "needs_review": needs_review,
        "invalid_json": invalid,
        "prefiltered": prefiltered,
        "duration_seconds": round(duration, 2),
    }
    job.status = InferenceJobStatus.COMPLETED
    job.result_json = stats
    job.completed_at = datetime.now(UTC)
    # m10 : l'inférence terminée marque le projet COMPLETED (réservé au MODULE 4).
    project.status = ProjectStatus.COMPLETED
    await session.commit()
    return stats


async def _progress(
    session: AsyncSession, job: InferenceJob, done: int, total: int, invalid: int, counts: dict
) -> None:
    """Progression lisible par GET /inference pendant le RUNNING (+ commit par article)."""
    job.result_json = {
        "phase": "running",
        "done": done + invalid,
        "total": total,
        "invalid_json": invalid,
        **counts,
    }
    await session.commit()


async def _make_mailer(session: AsyncSession) -> Mailer | None:
    """Mailer depuis les settings (DB > env) ; ``None`` si config invalide/absente.

    Best-effort : un échec de config ne casse pas le job (la notification est
    simplement skipée).
    """
    try:
        db_settings = {key: await get_setting(session, key) for key in _SMTP_SETTING_KEYS}
        return Mailer.from_settings(get_settings(), db_settings)
    except Exception:
        logger.warning("inference: SMTP config invalide, notification e-mail skipée")
        return None


async def _record_failure(session: AsyncSession, job: InferenceJob, exc: Exception) -> None:
    """Échec : job FAILED (best effort)."""
    logger.error("inference failed (job %s): %s", job.id, _sanitize(exc))
    try:
        await session.rollback()
        job.status = InferenceJobStatus.FAILED
        job.error = _sanitize(exc)
        job.completed_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        logger.exception("failed to record inference failure")


def _sanitize(exc: BaseException) -> str:
    """Message court (pas de traceback brut, pas d'URL ni secret)."""
    return str(exc)[:500] or type(exc).__name__
