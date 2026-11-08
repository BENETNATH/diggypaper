"""Corpus watch (veille automatisée, spec 2026-11-07-veille-automatisee).

Re-runs a source query, imports the new articles (``origin="watch"``), analyzes
them against the watch's search (a pass search whose frozen lot = the new
articles), and alerts the lead when some match.

Reuses the import unit (``_insert_article``) and the inference launch
(``start_ready`` pattern: frozen lot + prompt + ``inference_task.delay``). The
match + e-mail run in a post-inference hook (``evaluate_watch_matches``): the
alert is a signal, never a decision — the imported articles follow the existing
human review flow, unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.models import (
    Article,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    Project,
    Search,
    SearchArticle,
    User,
    Watch,
    WatchStatus,
)
from app.i18n import t
from app.metrics import RELEVANT_AT, as_bool, confidence
from app.searches import in_search
from app.sources import DateBounds
from app.tasks import (
    ADAPTERS,
    _chunks,
    _insert_article,
    _make_session_factory,
    _session_factory_override,
)

logger = logging.getLogger(__name__)

#: Max new ids fetched per pass (bounded lot — cost control, spec Risques).
WATCH_CAP = 200

#: Answers that count as "positive" for the primary field (spec §3).
_POSITIVE = {
    "yes",
    "oui",
    "true",
    "1",
    "vrai",
    "include",
    "pertinent",
    "pertinents",
    "relevant",
    "pertinent(s)",
}


# --- Match rule (pure, tested out of the database) --------------------------


def primary_field(fields: list[dict] | None) -> dict | None:
    """The primary field: the one marked ``primary``, else the first, else None."""
    if not fields:
        return None
    for f in fields:
        if f.get("primary"):
            return f
    return fields[0]


def is_positive(value, field_type: str | None) -> bool:
    """Whether an answer counts as "positive" (yes / include / pertinent / true)."""
    if value is None:
        return False
    if field_type == "boolean":
        return as_bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _POSITIVE
    return bool(value)


def article_matches(decision: dict | None, primary: dict | None, threshold: float) -> bool:
    """Watch match rule (§3): the primary field is answered positively, OR the
    confidence is at/above the threshold. A decision without either is no match."""
    if not decision:
        return False
    conf = confidence(decision.get("confidence_score"))
    if conf is not None and conf >= threshold:
        return True
    if primary is not None and primary.get("key") in decision:
        return is_positive(decision[primary["key"]], primary.get("type"))
    return False


def _threshold(watch: Watch) -> float:
    """The match threshold: the watch's, else the project triage default (0.5)."""
    return watch.match_threshold if watch.match_threshold is not None else RELEVANT_AT


# --- Configurable criteria match engine (spec 2026-11-08-criteres-veille) ----


def criterion_key(criterion: dict, index: int) -> str:
    """Decision key of a criterion: its field for ``kind="field"``, the stable
    ``watch_q_<index>`` (index of the criterion in the criteria list) for a
    free question."""
    if criterion.get("kind") == "free":
        return f"watch_q_{index}"
    return criterion["field_key"]


def criterion_met(decision: dict | None, criterion: dict, key: str, pass_keys: set[str]) -> bool:
    """Does the article decision satisfy one criterion?

    Snapshot guard: the key must be a field of the pass (``pass_keys``) — a
    field removed from the search since the pass was launched never matches,
    never raises. The answer must equal ``expected`` (strict bool for boolean
    fields, case-insensitive for choice), and the article confidence must fall
    inside the optional [conf_min, conf_max] window (a bound set with no
    confidence is not satisfied).
    """
    if decision is None or key not in pass_keys or key not in decision:
        return False
    value = decision[key]
    if isinstance(criterion.get("expected"), bool):
        # Boolean criterion (field of type boolean, or free question):
        # strict bool equality — a string "yes" never satisfies it.
        if not (isinstance(value, bool) and value == criterion["expected"]):
            return False
    else:
        # Choice field: case-insensitive equality with the expected option.
        if str(value).strip().lower() != str(criterion["expected"]).strip().lower():
            return False
    conf = confidence(decision.get("confidence_score"))
    if criterion.get("conf_min") is not None and (conf is None or conf < criterion["conf_min"]):
        return False
    if criterion.get("conf_max") is not None and (conf is None or conf > criterion["conf_max"]):
        return False
    return True


def watch_matches(
    criteria: list[dict] | None,
    combine: str | None,
    decision: dict | None,
    pass_fields: list[dict] | None,
    legacy_threshold: float,
    primary: dict | None,
) -> bool:
    """Match rule of a watch: with criteria, all (AND) or one (OR) of them is
    satisfied; without (None/empty) the legacy rule — unchanged."""
    if not criteria:
        return article_matches(decision, primary, legacy_threshold)
    pass_keys = {f["key"] for f in pass_fields or []}
    met = [
        criterion_met(decision, c, criterion_key(c, i), pass_keys) for i, c in enumerate(criteria)
    ]
    return any(met) if combine == "OR" else all(met)


def free_question_fields(criteria: list[dict] | None) -> list[dict]:
    """Pass-search fields of the watch's free questions: one boolean field
    ``watch_q_<i>`` per free criterion, in list order (stable index)."""
    return [
        {"key": f"watch_q_{i}", "type": "boolean", "label": c.get("question", "")}
        for i, c in enumerate(criteria or [])
        if c.get("kind") == "free"
    ]


# --- Import of the fresh ids (reuses _insert_article) -----------------------


async def _import_fresh(
    db: AsyncSession,
    project: Project,
    source: str,
    fresh: list[str],
    client: httpx.AsyncClient | None,
    adapter,
) -> list[uuid.UUID]:
    """Imports the fresh ids with ``origin="watch"`` (reusing ``_insert_article``).
    Returns the ids of the articles actually inserted."""
    by_id: dict[str, dict] = {}
    for batch in _chunks(fresh, 50):
        try:
            metas = await adapter.fetch_metadata(batch)
        except Exception:
            logger.exception("watch: metadata fetch failed (%d ids)", len(batch))
            continue
        by_id.update({m["native_id"]: m for m in metas if m.get("native_id")})
    for nid in fresh:
        meta = by_id.get(nid)
        if meta is None:
            continue
        meta |= {"source": source, "native_id": nid, "origin": "watch"}
        await _insert_article(db, project.id, meta, client, adapter)
    if not fresh:
        return []
    rows = (
        await db.execute(
            select(Article.id).where(
                Article.project_id == project.id,
                Article.source == source,
                Article.native_id.in_(fresh),
            )
        )
    ).scalars()
    return list(rows)


# --- Inference launch on the new articles (start_ready pattern) ------------


async def _resolve_model(db: AsyncSession, project: Project, watch: Watch) -> str | None:
    """The analysis model: the last job's model if it is still ACTIVE, else the
    first active registered model, else None (the pass imports but does not
    analyze). Inactive models (e.g. a retired one) are never launched."""
    last = (
        await db.execute(
            select(InferenceJob.model_used)
            .where(
                InferenceJob.project_id == project.id,
                in_search(InferenceJob.search_id, watch.search),
            )
            .order_by(InferenceJob.created_at.desc(), InferenceJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last:
        still_active = (
            await db.execute(
                select(ModelRegistry.name).where(
                    ModelRegistry.name == last, ModelRegistry.is_active.is_(True)
                )
            )
        ).first()
        if still_active is not None:
            return last
    first = (
        await db.execute(
            select(ModelRegistry.name)
            .where(ModelRegistry.is_active.is_(True))
            .order_by(ModelRegistry.name)
            .limit(1)
        )
    ).scalar_one_or_none()
    return first


async def _launch_pass_inference(
    db: AsyncSession,
    watch: Watch,
    project: Project,
    article_ids: list[uuid.UUID],
    user: User | None,
) -> InferenceJob | None:
    """Creates a pass search (same question/fields as the watch's search, frozen
    lot = the new articles) and launches the inference on it. Returns the job, or
    None if no model is available (import only)."""
    from app.api.v1.prompts import ensure_prompt
    from app.tasks.inference import inference_task

    search = await db.get(Search, watch.search_id)
    if search is None:
        return None
    model = await _resolve_model(db, project, watch)
    if model is None:
        logger.warning("watch %s: no model available, imported without analysis", watch.id)
        return None
    pass_search = Search(
        project_id=project.id,
        name=f"Veille {watch.source} {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}",
        question=search.question,
        # The watch's free questions ride along as boolean pass fields
        # (spec 2026-11-08-criteres-veille); the search fields are untouched.
        fields=[*(search.fields or []), *free_question_fields(watch.criteria)],
        text_sections=search.text_sections,
        read_windows=search.read_windows,
        kind="standard",
        lot_all=False,
        created_by=watch.created_by,
    )
    db.add(pass_search)
    await db.flush()
    db.add_all(SearchArticle(search_id=pass_search.id, article_id=i) for i in article_ids)
    prompt = await ensure_prompt(db, project, pass_search, None)
    job = InferenceJob(
        project_id=project.id,
        search_id=pass_search.id,
        options={"gold_examples": 0, "validate": False},
        prompt_template_id=prompt.id,
        model_used=model,
        prefilter_model=None,
        status=InferenceJobStatus.PENDING,
        created_by=watch.created_by,
    )
    db.add(job)
    await db.flush()  # job.id (Python-side uuid4 default) only exists after the flush
    watch.inference_job_id = job.id
    await db.commit()
    await db.refresh(job)
    inference_task.delay(str(job.id))
    logger.info(
        "watch %s: analysis started (job %s, %d articles)", watch.id, job.id, len(article_ids)
    )
    return job


# --- One watch pass ---------------------------------------------------------

ImportFresh = Callable[..., Awaitable[list[uuid.UUID]]]
LaunchInference = Callable[..., Awaitable[InferenceJob | None]]


class WatchCriteriaError(ValueError):
    """The watch's search has no question and no answer fields: nothing to
    analyze or match against. The pass refuses to run (the API maps it to a
    422; the beat logs and moves on)."""


def _has_criteria(search: Search | None) -> bool:
    if search is None:
        return False
    return bool((search.question or "").strip()) or bool(search.fields)


async def run_watch_pass(
    db: AsyncSession,
    watch: Watch,
    *,
    client: httpx.AsyncClient | None = None,
    adapter=None,
    import_fresh: ImportFresh | None = None,
    launch_inference: LaunchInference | None = None,
    cap: int = WATCH_CAP,
) -> dict:
    """One watch pass: re-push the query, dedup, import the fresh (origin="watch"),
    launch the analysis on the new articles (frozen lot) and record the run.

    ``last_run_at`` is advanced after the re-push (not after the analysis), so a
    slow inference never delays the next pass. The match + e-mail are evaluated in
    the post-inference hook, not here.
    """
    project = await db.get(Project, watch.project_id)
    if project is None:
        return {"error": "project_not_found"}
    # The criteria guard: without a question or answer fields there is nothing
    # to analyze or match — refuse before re-pushing the source (422 via the
    # API, logged by the beat).
    search = await db.get(Search, watch.search_id)
    if not _has_criteria(search):
        raise WatchCriteriaError(
            f"watch {watch.id}: search without criteria (no question, no fields)"
        )
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=60.0)
    try:
        if adapter is None:
            from app.settings.service import get_setting

            key = _source_key(watch.source)
            api_key = await get_setting(db, key) if key else None
            if watch.source not in ADAPTERS:
                return {"error": "unknown_source"}
            adapter = ADAPTERS[watch.source](client, api_key)
        bounds = DateBounds(date_from=watch.date_from, date_to=None)
        new_ids = [nid async for nid in adapter.iter_ids(watch.query, bounds, limit=cap)]

        # Dedup: the ids already in the project (by source/native_id) are set aside —
        # the plain re-import path (no double import, spec Non vérifié).
        present = {
            (src, nid)
            for src, nid in (
                await db.execute(
                    select(Article.source, Article.native_id).where(
                        Article.project_id == project.id
                    )
                )
            ).all()
        }
        fresh = [nid for nid in new_ids if (watch.source, str(nid)) not in present]

        watch.last_run_at = datetime.now(UTC)
        if not fresh:
            watch.last_matched = {
                "run_at": watch.last_run_at.isoformat(),
                "found": len(new_ids),
                "matched": 0,
                "new": [],
            }
            await db.commit()
            logger.info("watch %s: no new article (found=%d)", watch.id, len(new_ids))
            return {
                "found": len(new_ids),
                "fresh": 0,
                "imported": 0,
                "job_id": None,
                "search_id": None,
            }

        importer = import_fresh or _import_fresh
        article_ids = await importer(db, project, watch.source, fresh, client, adapter)
        if article_ids:
            from app.tasks.vectorize import vectorize_task

            # RAG: embed the freshly imported sections (spec 2026-11-07 §6)
            vectorize_task.delay(str(project.id), [str(i) for i in article_ids])
        if not article_ids:
            watch.last_matched = {
                "run_at": watch.last_run_at.isoformat(),
                "found": len(new_ids),
                "matched": 0,
                "new": [],
                "note": "budget_or_fetch_failed",
            }
            await db.commit()
            return {
                "found": len(new_ids),
                "fresh": len(fresh),
                "imported": 0,
                "job_id": None,
                "search_id": None,
            }

        launcher = launch_inference or _launch_pass_inference
        creator = await db.get(User, watch.created_by) if watch.created_by else None
        job = await launcher(db, watch, project, article_ids, creator)
        await db.commit()
        watch.last_matched = {
            "run_at": watch.last_run_at.isoformat(),
            "found": len(new_ids),
            "imported": len(article_ids),
            "new": [str(i) for i in article_ids],
            "job_id": str(job.id) if job else None,
        }
        await db.commit()
        return {
            "found": len(new_ids),
            "fresh": len(fresh),
            "imported": len(article_ids),
            "job_id": str(job.id) if job else None,
            "search_id": str(job.search_id) if job else None,
        }
    finally:
        if owns_client:
            await client.aclose()


def _source_key(source: str) -> str | None:
    """The DB setting key carrying the source API key (empty = keyless source)."""
    return {"pubmed": "pubmed_api_key", "europepmc": "europepmc_api_key"}.get(source)


# --- API-triggered immediate pass -------------------------------------------


async def run_watch_now(db: AsyncSession, watch: Watch, launched_by: User | None = None) -> dict:
    """Runs one pass now (the ``/run`` endpoint): real wiring, own http client."""
    return await run_watch_pass(db, watch)


# --- Post-inference hook: match + e-mail ------------------------------------


async def evaluate_watch_matches(
    db: AsyncSession, job: InferenceJob, *, mailer=None, app_base_url: str = "http://localhost:8000"
) -> dict:
    """After an inference job finishes: if it was launched by a watch, evaluate the
    match rule on the new articles, fill ``last_matched`` and alert the lead.

    Best-effort: a failure (e.g. SMTP) never breaks the job — it is logged.
    """
    try:
        watch = (
            await db.execute(select(Watch).where(Watch.inference_job_id == job.id))
        ).scalar_one_or_none()
        if watch is None:
            return {}
        project = await db.get(Project, watch.project_id)
        if project is None:
            return {}
        search = await db.get(Search, job.search_id)
        primary = primary_field(search.fields if search else None)
        threshold = _threshold(watch)
        # The new articles of this pass (the pass search's frozen lot).
        lot = set(
            (
                await db.execute(
                    select(SearchArticle.article_id).where(SearchArticle.search_id == job.search_id)
                )
            ).scalars()
        )
        decisions = await _decisions_for(db, lot, job.search_id, search)
        pass_fields = search.fields if search else None
        pass_keys = {f["key"] for f in pass_fields or []}
        criteria = watch.criteria
        combine = watch.combine
        matched: list[dict] = []
        for aid in lot:
            d = decisions.get(aid)
            if watch_matches(criteria, combine, d, pass_fields, threshold, primary):
                art = await db.get(Article, aid)
                if art is None:
                    continue
                # The satisfied criteria (indices, stable) for the e-mail labels.
                met_idx = [
                    i
                    for i, c in enumerate(criteria or [])
                    if criterion_met(d, c, criterion_key(c, i), pass_keys)
                ]
                matched.append(
                    {
                        "article_id": str(aid),
                        "title": art.title,
                        "pmid": art.pmid,
                        "confidence": confidence((d or {}).get("confidence_score")),
                        "criteria": met_idx,
                    }
                )
        watch.last_matched = {
            **(watch.last_matched or {}),
            "matched": len(matched),
            "matched_ids": [m["article_id"] for m in matched],
            "matched_details": [
                {
                    "id": m["article_id"],
                    "title": m.get("title"),
                    "pmid": m.get("pmid"),
                    "confidence": m.get("confidence"),
                }
                for m in matched
            ],
        }
        await db.commit()
        await _watch_email(db, watch, project, search, matched, mailer, app_base_url)
        return {"matched": len(matched), "watch_id": str(watch.id)}
    except Exception:
        logger.exception("watch: match evaluation / e-mail failed (job %s)", job.id)
        return {}


async def _decisions_for(
    db: AsyncSession, lot: set[uuid.UUID], search_id: uuid.UUID, search: Search | None
) -> dict[uuid.UUID, dict | None]:
    """Latest usable decision of each lot article on the pass search."""
    from app.db.models import ArticlePrediction, PredictionStatus
    from app.searches import in_search as _in_search

    rows = (
        await db.execute(
            select(ArticlePrediction.article_id, ArticlePrediction.decision_json)
            .where(
                _in_search(ArticlePrediction.search_id, search)
                if search
                else ArticlePrediction.search_id == search_id,
                ArticlePrediction.article_id.in_(list(lot)),
                ArticlePrediction.status != PredictionStatus.ERROR,
            )
            .order_by(ArticlePrediction.created_at.desc(), ArticlePrediction.id.desc())
        )
    ).all()
    out: dict[uuid.UUID, dict | None] = {}
    for aid, dj in rows:  # first per article = latest
        out.setdefault(aid, dj)
    return out


def _criterion_interval(criterion: dict) -> str:
    """Compact confidence window of a criterion: ``0.7–1``, ``0.7–``, ``–0.9`` or ``""``."""
    lo, hi = criterion.get("conf_min"), criterion.get("conf_max")
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}"
    if lo is not None:
        return f"{lo:g}–"
    if hi is not None:
        return f"–{hi:g}"
    return ""


def _criterion_label(criterion: dict, index: int, pass_fields: list[dict] | None, lang: str) -> str:
    """Compact i18n label of a criterion for the e-mail and the UI card."""
    expected = criterion.get("expected")
    if expected is True:
        ans = t(lang, "watch_yes")
    elif expected is False:
        ans = t(lang, "watch_no")
    else:
        ans = str(expected)
    iv = _criterion_interval(criterion)
    if criterion.get("kind") == "free":
        label = t(lang, "watch_crit_free", question=criterion.get("question", ""), expected=ans)
    else:
        field = next(
            (f for f in pass_fields or [] if f.get("key") == criterion.get("field_key")), None
        )
        label = t(
            lang,
            "watch_crit_field",
            label=(field.get("label") if field else None) or str(criterion.get("field_key", "")),
            expected=ans,
        )
    return f"{label} [{iv}]" if iv else label


async def _watch_email(
    db: AsyncSession,
    watch: Watch,
    project: Project,
    search: Search | None,
    matched: list[dict],
    mailer,
    app_base_url: str,
) -> None:
    """Alerts the lead (best-effort). With matches: the list + link. Without, only
    if ``notify_all``: a short "imported, no match" note. A send failure is logged."""
    if mailer is None:
        return
    lead = await db.get(User, project.created_by) if project.created_by else None
    if lead is None or not lead.email:
        logger.warning("watch %s: no lead e-mail, alert skipped", watch.id)
        return
    lang = lead.language_preference or "en"
    link = f"{app_base_url.rstrip('/')}/projects/{project.id}"
    name = search.name if search else ""
    imported = (watch.last_matched or {}).get("imported", len(matched))
    if matched:
        lines = []
        criteria = watch.criteria
        pass_fields = search.fields if search else None
        for m in matched:
            conf = f" ({m['confidence']:.2f})" if m.get("confidence") is not None else ""
            lines.append(f"- {m.get('title') or m['article_id']} [{m.get('pmid') or ''}]{conf}")
            # Met criteria, one compact label per line (criteria-based watches).
            for i in m.get("criteria") or []:
                label = _criterion_label(criteria[i], i, pass_fields, lang)
                lines.append(f"    — {label}")
        subject = t(lang, "watch_alert_subject", m=str(len(matched)), search_name=name)
        body = t(
            lang,
            "watch_alert_body",
            project_title=project.title,
            search_name=name,
            n=str(imported),
            m=str(len(matched)),
            matches="\n".join(lines) or "—",
            link=link,
        )
    elif watch.notify_all:
        subject = t(lang, "watch_import_subject", n=str(imported))
        body = t(
            lang,
            "watch_import_body",
            project_title=project.title,
            search_name=name,
            n=str(imported),
            link=link,
        )
    else:
        logger.info("watch %s: no match and notify_all=false — silence", watch.id)
        return
    try:
        await mailer.send(lead.email, subject, body)
        logger.info("watch %s: alert sent to %s (%d matched)", watch.id, lead.email, len(matched))
    except Exception:
        logger.exception("watch %s: e-mail send failed (best-effort)", watch.id)


# --- Beat task --------------------------------------------------------------


async def _run_all(session_factory=None) -> dict:
    engine = None
    sf = session_factory or _session_factory_override
    if sf is None:
        engine, sf = _make_session_factory()
    summary = {"watches": 0, "errors": 0}
    try:
        async with sf() as session:
            watches = (
                (
                    await session.execute(
                        select(Watch).where(Watch.status == WatchStatus.ACTIVE).order_by(Watch.id)
                    )
                )
                .scalars()
                .all()
            )
            for w in watches:
                try:
                    await run_watch_pass(session, w)
                    summary["watches"] += 1
                except Exception:
                    summary["errors"] += 1
                    logger.exception("watch %s: pass failed", w.id)
    finally:
        if engine is not None:
            await engine.dispose()
    return summary


@celery_app.task(name="diggypaper.watch")
def watch_task() -> dict:
    """Celery beat (1 pass/day): runs every active watch. One worker → no
    inter-pass lock; a pass never overlaps itself (Celery beat behavior)."""
    result = asyncio.run(_run_all())
    logger.info("watch beat: %s", result)
    return result
