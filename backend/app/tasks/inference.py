"""Mass inference task (MODULE 4, lot 5).

Same execution pattern as ``benchmark_task`` / ``import_pubmed_task`` (C1/C2):
sync Celery wrapper → dedicated thread → ``asyncio.run`` of the async core; engine
per run (or ``session_factory`` injected in tests).

The inference runs the validated prompt on **every article** of the corpus (FROZEN),
stores the result in ``article_predictions``, and applies the **automatic
triage** according to the confidence score (spec MODULE 4):

- ``confidence_score > 0.85`` → ``AUTO_ACCEPTED``
- ``confidence_score < 0.15`` → ``AUTO_REJECTED``
- else (or missing score) → ``NEEDS_HUMAN_REVIEW``

On completion, the job goes ``COMPLETED`` (statistics in ``result_json``) and a
notification is sent (best-effort email via ``notify_job_complete``).

Traceability (spec 2026-10-29): after the warm-up, ``job.model_info`` freezes a snapshot of
the models (main + prefilter: identifier, version/digest); each prediction carries
``model_identifier`` / ``model_version`` of the model that produced it (best-effort,
never blocking).
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
    model_snapshot,
    warmup,
)
from app.schema import project_fields
from app.searches import ScopedProject, get_search, gold_condition, in_search, lot_condition
from app.settings.service import get_setting
from app.tasks.notify import notify_job_complete

#: SMTP keys read from the DB for ``Mailer.from_settings`` (DB > env).
_SMTP_SETTING_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from_email",
    "use_tls",
)

logger = logging.getLogger(__name__)

#: Test hook: injected session_factory for the real thread+asyncio.run path.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

#: Triage bounds (spec MODULE 4).
_AUTO_ACCEPT_THRESHOLD = 0.85
_AUTO_REJECT_THRESHOLD = 0.15

#: Per-model lock (local backends): requeue 30 s; wait horizon = 24 h (aligned with
#: the Celery ``visibility_timeout``, a CPU job lasts hours); TTL = timeout +
#: anti-leak margin, renewed before each model call (worker crash → the lock expires).
_MODEL_BUSY_RETRY_SECONDS = 30
_MODEL_BUSY_MAX_RETRIES = 24 * 3600 // _MODEL_BUSY_RETRY_SECONDS
_MODEL_BUSY_TTL_GRACE_SECONDS = 300


class ModelBusy(Exception):
    """Local model already used by another job (Redis lock) → requeue the job."""


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


REJECTED = PredictionStatus.AUTO_REJECTED


def _triage(confidence_score) -> PredictionStatus:
    """Automatic triage according to the confidence score (spec MODULE 4).

    - ``> 0.85`` → ``AUTO_ACCEPTED``
    - ``< 0.15`` → ``AUTO_REJECTED``
    - else (bounds included, missing or non-numeric score) → ``NEEDS_HUMAN_REVIEW``
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
    """Sync Celery wrapper: runs the async core in a dedicated thread (C1/C2).

    ``ModelBusy`` (local model locked by another job): requeue after
    ``_MODEL_BUSY_RETRY_SECONDS`` s, up to ``_MODEL_BUSY_MAX_RETRIES``; beyond that,
    the job goes FAILED and the exception is re-raised.
    """
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_run_inference(inference_job_id))
        except BaseException as exc:  # propagated to the main thread
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
    """Async core of the mass inference.

    - ``session_factory`` injectable (tests); else engine per run (C2).
    - ``inference`` injectable (tests); else ``make_inference_service``.
    - ``redis`` injectable (tests); else on-demand client for the per-model lock.
    - Per-model lock (local backends: main + prefilter, sorted keys, all-or-nothing):
      if a model is busy → ``ModelBusy`` (job PENDING, phase ``waiting_model``),
      requeue to the wrapper. The locks are renewed before each model call
      (``heartbeat``).
    - Any other exception before the end of the job (Redis acquisition included)
      → FAILED.
    - Sequential per article (CPU-friendly, spec).
    - Returns the key statistics (``processed``, ``auto_accepted``, ``auto_rejected``,
      ``needs_review``, ``duration_seconds``).
    """
    if session_factory is None:
        session_factory = _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    client = http if http is not None else None
    locks: list[tuple[str, str]] = []  # (key, token) of the per-model locks acquired
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
                # Redelivery: job already closed, or already in flight in another process
                # (a worker restart first goes RUNNING → FAILED).
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
                # Per-model lock (local backends: main + prefilter): makes the
                # serialized wait in Ollama visible. All-or-nothing: one refused lock →
                # the ones already taken are released (finally) and the job waits. The
                # acquisition is before RUNNING: a waiting job stays PENDING in the
                # database (the "one job per project" guard is kept).
                keys: list[str] = []
                for name in (job.model_used, job.prefilter_model):
                    m = registry.get(name) if name else None
                    if m is None or m.backend_type == "OPENAI_API":
                        continue
                    key = model_lock_key(m.backend_type, m.model_identifier)
                    if key not in keys:  # same model on both sides: a single key
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
                # fixed order: no livelock between jobs with inverted main/prefilter roles
                for key in sorted(keys):
                    token = uuid.uuid4().hex
                    try:
                        acquired = await redis_client.set(key, token, nx=True, ex=ttl)
                    except RedisError as exc:
                        # internal host:port: in the log only, never in job.error
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
                return stats  # stopped by the user: no "finished" e-mail

            # E-mail notification (best-effort, never breaks the job).
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
            except Exception:  # noqa: BLE001 - closing the client must not break the job
                pass
        if engine is not None:
            await engine.dispose()


async def _lock_ttl(session: AsyncSession) -> int:
    """Lock TTL = ``inference_timeout`` (same parser as make_inference_service,
    default 120 s) + anti-leak margin: worker crash → the lock will expire at the TTL."""
    return int(await get_inference_timeout(session)) + _MODEL_BUSY_TTL_GRACE_SECONDS


async def _renew_locks(redis_client, locks: list[tuple[str, str]], ttl: int) -> None:
    """Renews the TTL of each held lock (GET == token → EXPIRE, same TTL as at
    acquisition). Best-effort like the release: different token or Redis in error →
    warning, the job continues."""
    # ponytail: GET then EXPIRE not atomic, Lua compare-and-set if the contention justifies it
    for key, token in locks:
        try:
            current = await redis_client.get(key)
            if isinstance(current, bytes):
                current = current.decode()
            if current == token:
                await redis_client.expire(key, ttl)
            else:
                logger.warning("inference: lock %s lost (different token), not renewed", key)
        except Exception:  # noqa: BLE001
            logger.warning("inference: renewing lock %s failed", key)


async def _release_lock(redis_client, key: str, token: str) -> None:
    """Lock release: DEL only if the current value is our token
    (owner check); called in finally (success, failure, cancellation).

    A failure (Redis unreachable) is logged, not re-raised: the TTL will expire the lock.
    """
    try:
        current = await redis_client.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:
            await redis_client.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("inference: releasing lock %s failed (the TTL will expire)", key)


async def _mark_model_busy_failed(inference_job_id: str) -> None:
    """Retries exhausted: job FAILED (same pattern as ``_record_failure``)."""
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
                f"model busy: abandoned after {_MODEL_BUSY_MAX_RETRIES} attempts (waiting_model)"
            )
            job.completed_at = datetime.now(UTC)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("inference: marking FAILED (model busy, retries exhausted) failed")
    finally:
        if engine is not None:
            await engine.dispose()


async def _start_scheduled(session: AsyncSession, project_id) -> None:
    """Starts the next scheduled analysis, if one is ready (best-effort)."""
    from app.scheduler import start_ready

    try:
        project = await session.get(Project, project_id)
        if project is not None:
            await start_ready(session, project)
    except Exception:  # noqa: BLE001 - must not fail the analysis that just finished
        logger.exception("scheduler: starting the scheduled analysis failed")


async def _do_inference(
    session: AsyncSession,
    job: InferenceJob,
    inference: OpenAICompatibleService,
    heartbeat=None,
) -> dict:
    """Inference body: for each article, inference + triage + persistence.

    ``heartbeat`` (async, optional): renews the per-model locks; called just before
    each model call (warm-ups and ``predict``)."""
    prompt = await session.get(PromptTemplate, job.prompt_template_id)
    if prompt is None:
        raise LookupError(f"prompt template not found: {job.prompt_template_id}")

    base_project = await session.get(Project, job.project_id)
    if base_project is None:
        raise LookupError(f"project not found: {job.project_id}")
    search = await get_search(session, base_project, job.search_id)
    # question, items and text parts: those of the search (the project for the rest)
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

    # Gold: examples added to the prompt ("educate" the model) and/or validation of the results.
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

    # Resume: articles already predicted (same prompt) since the interrupted job's date.
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

    # Registry models: stage 1 (small fast model, optional) and main model.
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
    # Cold start: the first call of a local model loads the weights in
    # Ollama (several minutes on CPU) — absorb it in the timeout budget
    # rather than making the first article pay for it.
    for m, name in ((pre, job.prefilter_model), (main, job.model_used)):
        if m is not None and m.backend_type == "OPENAI_API":
            continue  # remote backend: no local loading
        ident = m.model_identifier if m is not None else (name or "")
        if ident:
            if heartbeat:
                await heartbeat()
            await warmup(inference, ident, m)
    # Traceability: model snapshot (Ollama digest read once; best-effort).
    job.model_info = {
        "main": await model_snapshot(main, inference, job.model_used),
        "prefilter": (
            await model_snapshot(pre, inference, job.prefilter_model)
            if job.prefilter_model
            else None
        ),
    }
    # Visible right away (0 / N): the first call may download the model's weights.
    await _progress(session, job, 0, len(articles), 0, counts())

    async def predict(model, article, *, light: bool, meta: dict) -> dict:
        """Model call; ``light`` = stage 1 (title + abstract only); ``meta``
        receives ``served_model`` (version returned by the server)."""
        kinds = ["abstract"] if light else project.text_sections
        user_prompt = fill_prompt(prompt.user_prompt_schema, article, kinds)
        system = prompt.system_prompt or ""
        if shots and not light:
            system = (system + "\n\n" + shots).strip()
        if heartbeat:
            await heartbeat()
        out = await infer_with(
            model, inference, system, user_prompt, name=job.model_used, meta=meta
        )
        if not isinstance(out, dict):
            raise InferenceError("invalid_json")  # valid JSON but not an object
        return out

    def note_served(stage: str, meta: dict) -> None:
        """Non-Ollama backend: the stage's first served version fills the job's
        snapshot, even if that stage does not produce the prediction (a prefilter that
        never rejects)."""
        snap = job.model_info[stage] or {}
        version = meta.get("served_model")
        if version and not snap.get("version") and snap.get("backend") not in (None, "OLLAMA"):
            # new dict (no in-place mutation): otherwise SQLAlchemy does not see the change
            job.model_info = {**job.model_info, stage: {**snap, "version": version}}

    async def cancelled() -> bool:
        # Stop requested from the UI (POST …/inference/cancel: FAILED status in the database).
        status = (
            await session.execute(select(InferenceJob.status).where(InferenceJob.id == job.id))
        ).scalar_one_or_none()
        return status == InferenceJobStatus.FAILED

    for article in articles:
        if await cancelled():
            return {"cancelled": True, "processed": processed, **counts()}
        if article.id in done_status:  # already done before the interruption: count it, skip it
            st = done_status[article.id]
            auto_accepted += st == PredictionStatus.AUTO_ACCEPTED
            auto_rejected += st == REJECTED
            needs_review += st == PredictionStatus.NEEDS_HUMAN_REVIEW
            processed += 1
            continue
        model_name = job.model_used
        stage = "main"  # stage producing the prediction (traceability)
        prediction = None
        try:
            if pre is not None:
                # Stage 1: title + abstract; a clean rejection discards the article without stage 2.
                try:
                    meta = {}
                    p1 = await predict(pre, article, light=True, meta=meta)
                    note_served("prefilter", meta)
                except InferenceError as exc:
                    if str(exc) != "invalid_json":
                        raise
                    p1 = None  # stage 1 unreadable: the article goes to stage 2
                if p1 is not None and _triage(p1.get("confidence_score")) == REJECTED:
                    prediction, model_name, stage = p1, pre.name, "prefilter"
                    prefiltered += 1
            if prediction is None:
                meta = {}
                prediction = await predict(main, article, light=False, meta=meta)
                note_served("main", meta)
        except InferenceError as exc:
            # Small models: a non-JSON output must not kill the whole job;
            # timeout / connection / HTTP remain fatal.
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
        snap = job.model_info[stage] or {}
        if snap.get("backend") in (None, "OLLAMA"):
            version = snap.get("version")
        else:  # local /v1 or OPENAI_API: version = model returned by the server
            version = meta.get("served_model")
        session.add(
            ArticlePrediction(
                article_id=article.id,
                search_id=search.id,
                prompt_template_id=prompt.id,
                model_used=model_name,
                model_identifier=snap.get("identifier"),
                model_version=version,
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
        latest = {  # latest prediction of each batch article in this search
            r.article_id: r.decision_json or {}
            for r in (
                await session.execute(
                    select(ArticlePrediction)
                    .where(
                        in_search(ArticlePrediction.search_id, search),
                        ArticlePrediction.article_id.in_(in_lot),
                        # not the predictions of an earlier run (article with invalid output here)
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
    # m10: finished inference marks the project COMPLETED (reserved for MODULE 4).
    base_project.status = ProjectStatus.COMPLETED
    await session.commit()
    return stats


async def _progress(
    session: AsyncSession, job: InferenceJob, done: int, total: int, invalid: int, counts: dict
) -> None:
    """Progress readable via GET /inference while RUNNING (+ commit per article)."""
    job.result_json = {
        "phase": "running",
        "done": done + invalid,
        "total": total,
        "invalid_json": invalid,
        **counts,
    }
    await session.commit()


async def _make_mailer(session: AsyncSession) -> Mailer | None:
    """Mailer from the settings (DB > env); ``None`` if the config is invalid/absent.

    Best-effort: a config failure does not break the job (the notification is
    simply skipped).
    """
    try:
        db_settings = {key: await get_setting(session, key) for key in _SMTP_SETTING_KEYS}
        return Mailer.from_settings(get_settings(), db_settings)
    except Exception:
        logger.warning("inference: invalid SMTP config, e-mail notification skipped")
        return None


async def _record_failure(session: AsyncSession, job: InferenceJob, exc: Exception) -> None:
    """Failure: job FAILED (best effort)."""
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
    """Short message (no raw traceback, no URL or secret)."""
    return str(exc)[:500] or type(exc).__name__
