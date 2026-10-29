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
import os
import threading
import time
import uuid
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import metrics
from app.article_text import fill_prompt
from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    ArticlePrediction,
    GoldArticle,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
)
from app.emails import Mailer
from app.gold_examples import examples_block, pick_examples
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    get_inference_timeout,
    infer_with,
    make_inference_service,
    model_lock_key,
    warmup,
)
from app.schema import project_fields
from app.searches import ScopedProject, get_search, gold_condition, in_search, lot_condition
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

#: Lock par modèle (backends locaux) : requeue 30 s ; horizon d'attente = 24 h (aligné sur
#: le ``visibility_timeout`` Celery, un job CPU dure des heures) ; TTL = timeout + marge
#: anti-fuite, renouvelé avant chaque appel modèle (crash du worker → le lock expire).
_MODEL_BUSY_RETRY_SECONDS = 30
_MODEL_BUSY_MAX_RETRIES = 24 * 3600 // _MODEL_BUSY_RETRY_SECONDS
_MODEL_BUSY_TTL_GRACE_SECONDS = 300


class ModelBusy(Exception):
    """Modèle local déjà utilisé par un autre job (lock Redis) → requeue le job."""


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


REJECTED = PredictionStatus.AUTO_REJECTED


def _triage(confidence_score) -> PredictionStatus:
    """Triage automatique selon le score de confiance (spec MODULE 4).

    - ``> 0.85`` → ``AUTO_ACCEPTED``
    - ``< 0.15`` → ``AUTO_REJECTED``
    - sinon (bornes incluses, score absent ou non numérique) → ``NEEDS_HUMAN_REVIEW``
    """
    confidence_score = metrics.confidence(confidence_score)
    if confidence_score is None:
        return PredictionStatus.NEEDS_HUMAN_REVIEW
    if confidence_score > _AUTO_ACCEPT_THRESHOLD:
        return PredictionStatus.AUTO_ACCEPTED
    if confidence_score < _AUTO_REJECT_THRESHOLD:
        return PredictionStatus.AUTO_REJECTED
    return PredictionStatus.NEEDS_HUMAN_REVIEW


@celery_app.task(name="diggypaper.inference", bind=True)
def inference_task(self, inference_job_id: str) -> dict:
    """Wrapper sync Celery : exécute le core async dans un thread dédié (C1/C2).

    ``ModelBusy`` (modèle local locké par un autre job) : requeue
    ``_MODEL_BUSY_RETRY_SECONDS`` s, jusqu'à ``_MODEL_BUSY_MAX_RETRIES`` ; au-delà,
    le job passe FAILED puis l'exception est relancée.
    """
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
        if isinstance(error, ModelBusy):
            if self.request.retries >= _MODEL_BUSY_MAX_RETRIES:
                asyncio.run(_mark_model_busy_failed(inference_job_id))
            raise self.retry(
                countdown=_MODEL_BUSY_RETRY_SECONDS, max_retries=_MODEL_BUSY_MAX_RETRIES
            )
        raise error
    return result


async def _run_inference(
    inference_job_id: str,
    session_factory=None,
    http=None,
    inference: OpenAICompatibleService | None = None,
    redis=None,
) -> dict:
    """Core async de l'inférence massive.

    - ``session_factory`` injectable (tests) ; sinon engine par run (C2).
    - ``inference`` injectable (tests) ; sinon ``make_inference_service``.
    - ``redis`` injectable (tests) ; sinon client à la demande pour le lock par modèle.
    - Lock par modèle (backends locaux : principal + pré-filtre, clés triées, tout-ou-rien) : si un
      modèle est occupé → ``ModelBusy`` (job PENDING, phase ``waiting_model``), requeue au
      wrapper. Les locks sont renouvelés avant chaque appel modèle (``heartbeat``).
    - Toute autre exception avant la fin du job (acquisition Redis incluse) → FAILED.
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
    locks: list[tuple[str, str]] = []  # (key, token) des locks par modèle acquis
    redis_client = redis
    own_redis = False
    try:
        try:
            job_uuid = uuid.UUID(inference_job_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid inference job id: {inference_job_id}") from exc

        async with factory() as session:
            job = await session.get(InferenceJob, job_uuid)
            if job is None:
                raise LookupError(f"inference job not found: {inference_job_id}")
            if job.status in (
                InferenceJobStatus.FAILED,
                InferenceJobStatus.COMPLETED,
                InferenceJobStatus.RUNNING,
            ):
                # Redélivrance : job déjà clos, ou déjà en cours dans un autre processus
                # (un redémarrage du worker passe d'abord RUNNING → FAILED).
                return {}
            try:
                registry = {
                    m.name: m
                    for m in (
                        await session.execute(
                            select(ModelRegistry).where(
                                ModelRegistry.name.in_(
                                    [n for n in (job.model_used, job.prefilter_model) if n]
                                )
                            )
                        )
                    ).scalars()
                }
                # Lock par modèle (backends locaux : principal + pré-filtre) : rend visible
                # l'attente sérialisée dans Ollama. Tout-ou-rien : un lock refusé → ceux déjà
                # pris sont relâchés (finally) et le job attend. L'acquisition est avant
                # RUNNING : un job en attente reste PENDING en base (garde « un job par
                # projet » conservée).
                keys: list[str] = []
                for name in (job.model_used, job.prefilter_model):
                    m = registry.get(name) if name else None
                    if m is None or m.backend_type == "OPENAI_API":
                        continue
                    key = model_lock_key(m.backend_type, m.model_identifier)
                    if key not in keys:  # même modèle des deux côtés : une seule clé
                        keys.append(key)
                ttl = 0
                if keys:
                    if redis_client is None:
                        from redis import asyncio as redis_asyncio

                        redis_client = redis_asyncio.from_url(
                            os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                        )
                        own_redis = True
                    ttl = await _lock_ttl(session)
                # ordre fixe : pas de livelock entre jobs aux rôles principal/pré-filtre inversés
                for key in sorted(keys):
                    token = uuid.uuid4().hex
                    try:
                        acquired = await redis_client.set(key, token, nx=True, ex=ttl)
                    except RedisError as exc:
                        # host:port interne : au log seulement, jamais dans job.error
                        logger.warning("inference: lock %s indisponible (Redis) : %s", key, exc)
                        raise RuntimeError("model_lock_unavailable") from exc
                    if not acquired:
                        job.result_json = {"phase": "waiting_model"}
                        await session.commit()
                        raise ModelBusy(key)
                    locks.append((key, token))
                job.status = InferenceJobStatus.RUNNING
                await session.commit()

                async def heartbeat() -> None:
                    await _renew_locks(redis_client, locks, ttl)

                if inference is None:
                    inference = await make_inference_service(session, http=client)
                stats = await _do_inference(session, job, inference, heartbeat)
            except ModelBusy:
                raise
            except Exception as exc:
                await _record_failure(session, job, exc)
                raise
            if stats.get("cancelled"):
                return stats  # arrêtée par l'utilisateur : pas d'e-mail « terminée »

            # Notification e-mail (best-effort, ne casse pas le job).
            mailer = await _make_mailer(session)
            await notify_job_complete(
                str(job.project_id),
                "inference",
                stats,
                session_factory=factory,
                mailer=mailer,
            )
            await _start_scheduled(session, job.project_id)
            return stats
    finally:
        for key, token in locks:
            await _release_lock(redis_client, key, token)
        if own_redis:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001 - fermer le client ne doit pas casser le job
                pass
        if engine is not None:
            await engine.dispose()


async def _lock_ttl(session: AsyncSession) -> int:
    """TTL du lock = ``inference_timeout`` (même parseur que make_inference_service,
    défaut 120 s) + marge anti-fuite : crash du worker → le lock expirera au TTL."""
    return int(await get_inference_timeout(session)) + _MODEL_BUSY_TTL_GRACE_SECONDS


async def _renew_locks(redis_client, locks: list[tuple[str, str]], ttl: int) -> None:
    """Renouvelle le TTL de chaque lock détenu (GET == token → EXPIRE, même TTL qu'à
    l'acquisition). Best-effort comme la release : token différent ou Redis en erreur →
    warning, le job continue."""
    # ponytail: GET puis EXPIRE non atomique, Lua compare-and-set si la contention le justifie
    for key, token in locks:
        try:
            current = await redis_client.get(key)
            if isinstance(current, bytes):
                current = current.decode()
            if current == token:
                await redis_client.expire(key, ttl)
            else:
                logger.warning("inference: lock %s perdu (token différent), non renouvelé", key)
        except Exception:  # noqa: BLE001
            logger.warning("inference: renouvellement du lock %s en échec", key)


async def _release_lock(redis_client, key: str, token: str) -> None:
    """Release du lock : DEL seulement si la valeur courante est notre token
    (owner check) ; appelé en finally (succès, échec, annulation).

    Un échec (Redis injoignable) est loggé, pas relancé : le TTL expirera le lock.
    """
    try:
        current = await redis_client.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:
            await redis_client.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("inference: release du lock %s en échec (le TTL expirera)", key)


async def _mark_model_busy_failed(inference_job_id: str) -> None:
    """Retries épuisées : job FAILED (même pattern que ``_record_failure``)."""
    factory = _session_factory_override
    engine = None
    if factory is None:
        engine, factory = _make_session_factory()
    try:
        try:
            job_uuid = uuid.UUID(inference_job_id)
        except (TypeError, ValueError):
            return
        async with factory() as session:
            job = await session.get(InferenceJob, job_uuid)
            if job is None:
                return
            job.status = InferenceJobStatus.FAILED
            job.error = (
                f"modèle occupé : abandonné après {_MODEL_BUSY_MAX_RETRIES} tentatives "
                "(waiting_model)"
            )
            job.completed_at = datetime.now(UTC)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("inference: marquage FAILED (modèle occupé, retries épuisées) en échec")
    finally:
        if engine is not None:
            await engine.dispose()


async def _start_scheduled(session: AsyncSession, project_id) -> None:
    """Lance l'analyse programmée suivante, s'il y en a une prête (best-effort)."""
    from app.scheduler import start_ready

    try:
        project = await session.get(Project, project_id)
        if project is not None:
            await start_ready(session, project)
    except Exception:  # noqa: BLE001 - ne doit pas faire échouer l'analyse qui vient de finir
        logger.exception("scheduler: démarrage de l'analyse programmée en échec")


async def _do_inference(
    session: AsyncSession,
    job: InferenceJob,
    inference: OpenAICompatibleService,
    heartbeat=None,
) -> dict:
    """Corps de l'inférence : pour chaque article, inférence + triage + persistance.

    ``heartbeat`` (async, optionnel) : renouvelle les locks par modèle ; appelé juste avant
    chaque appel modèle (warm-ups et ``predict``)."""
    prompt = await session.get(PromptTemplate, job.prompt_template_id)
    if prompt is None:
        raise LookupError(f"prompt template not found: {job.prompt_template_id}")

    base_project = await session.get(Project, job.project_id)
    if base_project is None:
        raise LookupError(f"project not found: {job.project_id}")
    search = await get_search(session, base_project, job.search_id)
    # question, items et parties de texte : ceux de la recherche (le projet pour le reste)
    project = ScopedProject(base_project, search)

    articles = (
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

    # Gold : exemples ajoutés au prompt (« éduquer » le modèle) et/ou validation des résultats.
    opts = job.options or {}
    gold_all: dict = {}
    example_ids: list = []
    shots = ""
    if opts.get("gold_examples") or opts.get("validate"):
        gold_all = {
            g.article_id: g.ground_truth_json
            for g in (
                await session.execute(
                    select(GoldArticle).where(await gold_condition(session, base_project, search))
                )
            ).scalars()
            if g.article_id
        }
    if opts.get("gold_examples") and gold_all:
        lot = {a.id for a in articles}
        outside = {i: g for i, g in gold_all.items() if i not in lot}
        k = int(opts["gold_examples"])
        example_ids = pick_examples(outside if len(outside) >= k else gold_all, k, seed=1)
        ex = {
            a.id: a
            for a in (
                await session.execute(select(Article).where(Article.id.in_(example_ids)))
            ).scalars()
        }
        shots = examples_block(
            ex, gold_all, example_ids, project_fields(project), project.text_sections
        )

    # Reprise : articles déjà prédits (même prompt) depuis la date du job interrompu.
    done_status: dict = {}
    if job.resume_after is not None:
        rows = await session.execute(
            select(ArticlePrediction.article_id, ArticlePrediction.status).where(
                ArticlePrediction.prompt_template_id == prompt.id,
                in_search(ArticlePrediction.search_id, search),
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

    # Modèles du registre : étage 1 (petit modèle rapide, optionnel) et modèle principal.
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
    # Cold start : le premier appel d'un modèle local charge les poids dans
    # Ollama (plusieurs minutes sur CPU) — on l'absorbe dans le budget du
    # timeout plutôt que de le faire payer au premier article.
    for m, name in ((pre, job.prefilter_model), (main, job.model_used)):
        if m is not None and m.backend_type == "OPENAI_API":
            continue  # backend distant : pas de chargement local
        ident = m.model_identifier if m is not None else (name or "")
        if ident:
            if heartbeat:
                await heartbeat()
            await warmup(inference, ident, m)
    # Visible tout de suite (0 / N) : le premier appel peut télécharger les poids du modèle.
    await _progress(session, job, 0, len(articles), 0, counts())

    async def predict(model, article, *, light: bool) -> dict:
        """Appel du modèle ; ``light`` = étage 1 (titre + abstract seulement)."""
        kinds = ["abstract"] if light else project.text_sections
        user_prompt = fill_prompt(prompt.user_prompt_schema, article, kinds)
        system = prompt.system_prompt or ""
        if shots and not light:
            system = (system + "\n\n" + shots).strip()
        if heartbeat:
            await heartbeat()
        out = await infer_with(model, inference, system, user_prompt, name=job.model_used)
        if not isinstance(out, dict):
            raise InferenceError("invalid_json")  # JSON valide mais pas un objet
        return out

    async def cancelled() -> bool:
        # Arrêt demandé depuis l'interface (POST …/inference/cancel : statut FAILED en base).
        status = (
            await session.execute(select(InferenceJob.status).where(InferenceJob.id == job.id))
        ).scalar_one_or_none()
        return status == InferenceJobStatus.FAILED

    for article in articles:
        if await cancelled():
            return {"cancelled": True, "processed": processed, **counts()}
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
                try:
                    p1 = await predict(pre, article, light=True)
                except InferenceError as exc:
                    if str(exc) != "invalid_json":
                        raise
                    p1 = None  # étage 1 illisible : l'article passe à l'étage 2
                if p1 is not None and _triage(p1.get("confidence_score")) == REJECTED:
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
        confidence = metrics.confidence(prediction.get("confidence_score"))
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
                search_id=search.id,
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
    validation = None
    if opts.get("validate") and gold_all:
        in_lot = {a.id for a in articles}
        latest = {  # dernière prédiction de chaque article du lot dans cette recherche
            r.article_id: r.decision_json or {}
            for r in (
                await session.execute(
                    select(ArticlePrediction)
                    .where(
                        in_search(ArticlePrediction.search_id, search),
                        ArticlePrediction.article_id.in_(in_lot),
                        # pas les prédictions d'un run antérieur (article en sortie invalide ici)
                        ArticlePrediction.created_at >= (job.resume_after or job.created_at),
                    )
                    .order_by(ArticlePrediction.created_at.nulls_first(), ArticlePrediction.id)
                )
            ).scalars()
        }
        gold = {i: g for i, g in gold_all.items() if i in in_lot}
        scored = metrics.score(project_fields(project), latest, gold, exclude=set(example_ids))
        target, metric, value = metrics.primary_value(scored, project_fields(project))
        validation = {**scored, "primary": {"item": target, "metric": metric, "value": value}}
    stats = {
        "processed": processed,
        "auto_accepted": auto_accepted,
        "auto_rejected": auto_rejected,
        "needs_review": needs_review,
        "invalid_json": invalid,
        "prefiltered": prefiltered,
        "duration_seconds": round(duration, 2),
    }
    if example_ids:
        stats["gold_examples"] = len(example_ids)
    if validation is not None:
        stats["validation"] = validation
    job.status = InferenceJobStatus.COMPLETED
    job.result_json = stats
    job.completed_at = datetime.now(UTC)
    # m10 : l'inférence terminée marque le projet COMPLETED (réservé au MODULE 4).
    base_project.status = ProjectStatus.COMPLETED
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
