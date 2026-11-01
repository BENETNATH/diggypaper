"""Celery tasks: PubMed import (Module 2).

Execution pattern (C1/C2, empirically validated):

- The Celery task stays **sync** (prefork).
- It runs the async core (``_import_pubmed``) in a **dedicated thread** via
  ``asyncio.run``: no loop in flight in that thread → works in eager mode
  (FastAPI loop in flight in the calling thread) as in prod prefork (no
  reuse of an asyncpg engine bound to a closed loop).
- The core creates its **own engine per run** (from ``DATABASE_URL``, read at
  call time) and **disposes** it at the end of the run. If ``session_factory``
  is injected (tests), it does not create an engine.
- The httpx client is created by the core (explicit timeout, m4) and closed
  in ``finally``.
- Thread exceptions are propagated to the task (the Celery job goes FAILED;
  the import_jobs job is marked FAILED, the project becomes DRAFT again).

Project status semantics (m10): import starts → PROCESSING; success (including
an empty corpus) → FROZEN; failure → DRAFT. ``COMPLETED`` is reserved for
MODULE 4 (inference done).

TOCTOU (m11): between the two esearch calls (``search_volume`` then
``fetch_pmids``), the PubMed corpus can change — acceptable: the import is a
snapshot, the count is indicative (the result persists the real ``total_count``).
"""

import asyncio
import logging
import os
import re
import threading
import uuid
from datetime import UTC, date, datetime

import httpx
from celery import current_task
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.citations import fetch_links, rank
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    ImportJob,
    ImportJobStatus,
    Project,
    ProjectStatus,
    Search,
    SearchArticle,
)
from app.europepmc import EuropePMCAdapter
from app.fulltext import complete_article, long_enough, pmc_not_archived
from app.hal import HalAdapter
from app.openalex import OpenAlexAdapter
from app.pubmed import (
    NCBIError,
    PubMedAdapter,
    _esearch_max,
    fetch_fulltext,
    fetch_metadata,
)
from app.refimport import (
    _entry,
    clean_doi,
    crossref,
    dedupe,
    epmc_pmid_by_title,
    epmc_pmids_by_doi,
    label,
    norm_title,
)
from app.searches import default_search, definition_hash, next_search_name
from app.settings.service import get_setting
from app.sources import DateBounds, SourceAdapter

logger = logging.getLogger(__name__)

#: PubMed language (label) → ISO code.
#: m3: a language outside en/fr/de/es keeps its raw label (no forced
#: 'en') — the ``articles.original_language`` column is a free-form VARCHAR.
_LANGUAGE_MAP = {
    "english": "en",
    "french": "fr",
    "german": "de",
    "spanish": "es",
}

#: Search sources registry: code → factory ``(client, api_key) -> SourceAdapter``.
ADAPTERS = {
    "pubmed": PubMedAdapter,
    "europepmc": EuropePMCAdapter,
    "openalex": OpenAlexAdapter,
    "hal": HalAdapter,
}

#: Per-source API key setting (the other sources use none).
_SOURCE_KEY_SETTING = {"openalex": "openalex_api_key"}


def _source_key_setting(source: str) -> str:
    return _SOURCE_KEY_SETTING.get(source, "ncbi_api_key")


#: efetch batch size (pmids per request).
_EFETCH_BATCH = 100

#: Test hook: if set, the core uses it instead of creating its own
#: engine from DATABASE_URL (the Celery wrapper has no session parameter —
#: the hook exercises the real thread+asyncio.run path in tests with the
#: test SQLite engine).
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

#: C-1: masks the NCBI key in error messages (job.error + server log)
#: — ``api_key=…`` in the httpx URL (defense in depth: NCBIError is already
#: clean, this covers the other exceptions).
_API_KEY_RE = re.compile(r"api_key=[^&\s'\"<>]*")


def _sanitize_error(exc: BaseException) -> str:
    """C-1: clean error message for ``job.error`` + server log.

    - ``NCBIError`` → its message (already without URL or key).
    - else → filtered ``str(exc)`` (``api_key=…`` masked by regex), truncated to 500
      characters (no raw traceback with the URL).
    """
    if isinstance(exc, NCBIError):
        return str(exc) or type(exc).__name__
    message = _API_KEY_RE.sub("api_key=***", str(exc))
    return message[:500] or type(exc).__name__


def _map_language(language: str | None) -> str:
    """ISO code if known, else the raw PubMed label (m3); ``en`` if absent."""
    if not language:
        return "en"
    label = language.strip()
    return _LANGUAGE_MAP.get(label.lower(), label)


def _biblio(meta: dict) -> dict:
    """Bibliographic metadata of a PubMed article (journal, date, authors, types)."""
    date = meta.get("pub_date")
    return {
        "journal": meta.get("journal"),
        "pub_date": date,
        "pub_year": int(date[:4]) if date and date[:4].isdigit() else None,
        "authors": meta.get("authors") or None,
        "pub_types": meta.get("pub_types") or None,
    }


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _make_session_factory():
    """Engine per run (C2): created from DATABASE_URL (env, read at call time).

    The run disposes the engine at the end (``_import_pubmed``): no reuse of
    an engine bound to a closed event loop.
    """
    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


#: Celery queue of the tasks that talk to NCBI: a single worker consumes it, with
#: ``--concurrency=1`` — the ``app.pubmed`` throttle is per process (3 req/s keyless,
#: 10 with), so several processes would multiply it beyond the NCBI ceiling.
INGEST_QUEUE = "ingest"


@celery_app.task(name="diggypaper.import_pubmed", queue=INGEST_QUEUE)
def import_pubmed_task(
    project_id: str,
    query: str,
    refs: list[dict] | None = None,
    options: dict | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str = "pubmed",
) -> dict:
    """Sync Celery wrapper: runs the async core in a dedicated thread (C1/C2).

    ``asyncio.run`` runs in the worker thread (no loop in flight in that thread):
    OK in eager mode (FastAPI loop in flight in the calling thread) as in prod
    prefork. Thread exceptions are raised in the main thread (the Celery job goes
    FAILED).

    ``current_task`` is thread-local (Celery stack): the job id is read in the
    calling thread, before spawning the worker thread.
    """
    job_id = current_task.request.id
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(
                _import_pubmed(
                    project_id,
                    query,
                    job_id=job_id,
                    refs=refs,
                    options=options,
                    date_from=date_from,
                    date_to=date_to,
                    source=source,
                )
            )
        except BaseException as exc:  # propagated to the main thread
            error = exc

    thread = threading.Thread(target=_run, name="import-pubmed")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    # Missing full text (outside PMC): chained automatically, as a separate task.
    if result.get("imported") and not celery_app.conf.task_always_eager:
        complete_fulltext_task.delay(project_id)
    return result


async def _import_pubmed(
    project_id: str,
    query: str,
    session_factory=None,
    http: httpx.AsyncClient | None = None,
    job_id: str | None = None,
    refs: list[dict] | None = None,
    options: dict | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    adapter: SourceAdapter | None = None,
    source: str = "pubmed",
) -> dict:
    """Async core of the PubMed import (or, with ``refs``, of a references list).

    - ``session_factory`` injectable (tests); else the core creates its own engine per
      run (DATABASE_URL) and disposes it at the end (C2).
    - ``http`` injectable (tests); else ``httpx.AsyncClient(timeout=60.0)`` (m4), closed
      in ``finally``.
    - ``adapter`` (Phase 1): ``SourceAdapter`` contract; ``None`` → that of ``source`` in
      ``ADAPTERS`` (built in ``_do_import``). Not used by the refs/citations paths.
    - ``job_id``: Celery id of the task (persisted in import_jobs, M3).
    - Project status semantics (m10): start → PROCESSING; success (including an
      empty corpus) → FROZEN; failure → DRAFT.
    - Partial error (one pmid fails) → log, continue, do not block.
    - Returns ``{"imported", "failed", "total_count", "truncated"}`` (M1:
      ``total_count`` = real NCBI count, not ``len(pmids)``).
    """
    if session_factory is None:
        session_factory = _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    client = http if http is not None else httpx.AsyncClient(timeout=60.0)
    owns_client = http is None
    try:
        try:
            project_uuid = uuid.UUID(project_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid project id: {project_id}") from exc

        async with factory() as session:
            project = await session.get(Project, project_uuid)
            if project is None:
                raise LookupError(f"project not found: {project_id}")
            # M2: state guard — only a DRAFT project can be imported
            # (the API returns 409; this is the TOCTOU guard on the task side).
            if project.status == ProjectStatus.PROCESSING:
                raise LookupError(f"project {project_id} is already PROCESSING")
            # An import can complete an existing corpus: on failure we revert to it.
            previous = project.status

            # M3: the job is persisted (query + status + result/error).
            job = ImportJob(
                project_id=project.id,
                query=query,
                source=None if refs is not None else source,
                job_id=job_id,
                kind=(options or {}).get("kind")
                or ("references" if refs is not None else "import"),
            )
            session.add(job)
            await session.commit()  # PENDING (server_default)
            job.status = ImportJobStatus.RUNNING
            job.result_json = {"phase": "searching"}
            project.status = ProjectStatus.PROCESSING  # m10
            await session.commit()

            try:
                if options and options.get("citations"):
                    return await _do_import_citations(
                        session, project, job, options["citations"], client
                    )
                if refs is not None:
                    return await _do_import_refs(session, project, job, refs, client)
                return await _do_import(
                    session, project, job, query, client, date_from, date_to, adapter, source
                )
            except Exception as exc:
                await _record_failure(session, project, job, exc, restore=previous)
                raise
    finally:
        if owns_client:
            await client.aclose()
        if engine is not None:
            await engine.dispose()


async def _do_import(
    session: AsyncSession,
    project: Project,
    job: ImportJob,
    query: str,
    client: httpx.AsyncClient,
    date_from: date | None = None,
    date_to: date | None = None,
    adapter: SourceAdapter | None = None,
    source: str = "pubmed",
) -> dict:
    """Import body: esearch → efetch in batches → insertion (m6: commit per batch)."""
    api_key = await get_setting(session, _source_key_setting(source))
    if adapter is None:
        if source not in ADAPTERS:
            raise LookupError("unknown source")
        adapter = ADAPTERS[source](client, api_key)
    bounds = DateBounds(date_from, date_to)
    # m11: TOCTOU between the two esearch calls (count then idlist) — acceptable,
    # the import is a snapshot; ``total_count`` is the real count.
    count = await adapter.search_volume(query, bounds)
    if count == 0:
        return await _finish_success(
            session,
            project,
            job,
            {"imported": 0, "failed": 0, "fulltext": 0, "total_count": 0, "truncated": False},
        )
    # Cap absent → the adapter's default (``_esearch_max()``), as before.
    pmids = [nid async for nid in adapter.iter_ids(query, bounds)]
    truncated = count > _esearch_max()
    if truncated:
        logger.warning(
            "import truncated: count=%d > %d (project %s) — only the first %d articles "
            "are imported",
            count,
            _esearch_max(),
            project.id,
            len(pmids),
        )
    job.result_json = {"phase": "importing", "done": 0, "total": len(pmids)}
    await session.commit()
    imported = 0
    failed = 0
    fulltext = 0
    duplicates_doi = 0
    done = 0
    # DOIs carried by records of THIS import: a second identical DOI is skipped (not
    # attached — attachment only targets existing rows).
    import_dois: set[str] = set()
    # Normalized DOI (and PMID) → identities (source, native_id) already in the project:
    # an identifier carried by an EXISTING other identity is attached (archi § 3), not
    # re-inserted — the PMID map dedupes across sources when the DOI is unknown on one side.
    doi_owners: dict[str, set] = {}
    pmid_owners: dict[str, set] = {}
    for pmid, doi, src, nid in (
        await session.execute(
            select(Article.pmid, Article.doi, Article.source, Article.native_id).where(
                Article.project_id == project.id
            )
        )
    ).all():
        if key := clean_doi(doi):
            doi_owners.setdefault(key, set()).add((src, nid))
        if pmid:
            pmid_owners.setdefault(pmid, set()).add((src, nid))

    async def progress() -> None:
        # Readable by GET /jobs while RUNNING (import screen progress bar).
        job.result_json = {
            "phase": "importing",
            "done": done,
            "total": len(pmids),
            "imported": imported,
            "failed": failed,
            "fulltext": fulltext,
            "total_count": count,
        }
        await session.commit()  # m6: frequent commit (partial resilience)

    for batch in _chunks(pmids, _EFETCH_BATCH):
        try:
            metas = await adapter.fetch_metadata(batch)
        except Exception:
            logger.exception("efetch batch failed (%d pmids)", len(batch))
            failed += len(batch)
            done += len(batch)
            await progress()
            continue
        by_id = {m["native_id"]: m for m in metas if m.get("native_id")}
        for native_id in batch:
            meta = by_id.get(native_id)
            if meta is None:
                failed += 1
            else:
                try:
                    meta |= {
                        "source": source,
                        "native_id": native_id,
                        "origin": source,
                        "origin_ref": str(job.id),
                    }
                    identity, doi = (source, native_id), clean_doi(meta.get("doi"))
                    others = doi_owners.get(doi, set()) - {identity} if doi else set()
                    if not others and (pmid := meta.get("pmid")):
                        others = pmid_owners.get(pmid, set()) - {identity}
                    if others:
                        # DOI carried by another existing identity (archi § 3): attachment to the
                        # row (empty fields + full text if absent) — no duplicate.
                        await _link_doi_article(session, project.id, meta, others, client, adapter)
                        duplicates_doi += 1
                    elif doi and doi in import_dois:
                        # A record of this same import already carries the DOI: skip (no
                        # full text for nothing) — phase 2 behavior.
                        duplicates_doi += 1
                    else:
                        inserted, has_text = await _insert_article(
                            session, project.id, meta, client, adapter
                        )
                        if inserted:
                            imported += 1
                            fulltext += has_text
                            if doi:
                                import_dois.add(doi)
                        # else: duplicate (re-import) → neither imported nor failed (M2)
                except Exception:
                    logger.exception("article insert failed for %s %s", source, native_id)
                    failed += 1
            done += 1
            await progress()  # per article: the PMC full text takes ~0.3 s/article
    return await _finish_success(
        session,
        project,
        job,
        {
            "imported": imported,
            "failed": failed,
            "fulltext": fulltext,
            "total_count": count,
            "truncated": truncated,
        }
        | ({"duplicates_doi": duplicates_doi} if duplicates_doi else {}),
    )


async def _do_import_refs(
    session: AsyncSession,
    project: Project,
    job: ImportJob,
    refs: list[dict],
    client: httpx.AsyncClient,
    finish=None,
) -> dict:
    """References list (DOI, PMID, titles…) → corpus articles.

    ``finish`` (coroutine, optional): called before the job closes, its result (dict) is
    added to the result (e.g. search created on the new articles).

    Resolution: known PMID (or found via Europe PMC by DOI / exact title) → full
    PubMed record; else Crossref (DOI); else the file's metadata. The full text is
    sought afterwards (``complete_fulltext_task``), like after a PubMed import.
    """
    api_key = await get_setting(session, "ncbi_api_key")
    email = (
        await get_setting(session, "contact_email")
        or await get_setting(session, "smtp_from_email")
        or "diggypaper@example.org"
    )
    refs = dedupe(refs)
    truncated = len(refs) > _esearch_max()
    refs = refs[: _esearch_max()]
    # Already in the corpus (re-import): ignored, like PubMed duplicates.
    rows = (
        await session.execute(
            select(Article.pmid, Article.doi, Article.title).where(Article.project_id == project.id)
        )
    ).all()

    def keys(pmid, doi, title) -> set:
        return {k for k in (pmid, clean_doi(doi), norm_title(title)) if k}

    known = {k for row in rows for k in keys(*row)}
    done = imported = failed = fulltext = duplicates = 0
    sources = {"pubmed": 0, "crossref": 0, "file": 0}
    failed_items: list[str] = []

    async def progress() -> None:
        job.result_json = {
            "phase": "importing",
            "done": done,
            "total": len(refs),
            "imported": imported,
            "failed": failed,
            "fulltext": fulltext,
            "total_count": len(refs),
            "duplicates": duplicates,
            "sources": sources,
        }
        await session.commit()

    await progress()
    for chunk in _chunks(refs, _EFETCH_BATCH):
        by_doi = await epmc_pmids_by_doi(
            [e["doi"] for e in chunk if e["doi"] and not e["pmid"]], client
        )
        for e in chunk:
            e["pmid"] = e["pmid"] or by_doi.get(e["doi"] or "")
            if not (e["pmid"] or e["doi"]) and e["title"]:
                e["pmid"] = await epmc_pmid_by_title(e["title"], client)
        try:
            metas = {
                m["pmid"]: m
                for m in await fetch_metadata(
                    [e["pmid"] for e in chunk if e["pmid"]], api_key, client
                )
            }
        except Exception:
            logger.exception("efetch batch failed (references)")
            metas = {}
        for e in chunk:
            try:
                meta, source = metas.get(e["pmid"] or ""), "pubmed"
                if meta is None:
                    meta, source = await _file_meta(e, email, client)
                if meta is None:
                    failed += 1
                    failed_items.append(label(e))
                elif keys(meta.get("pmid"), meta.get("doi"), meta.get("title")) & known:
                    duplicates += 1
                else:
                    if not meta.get("doi"):
                        meta["doi"] = e["doi"]
                    # native identifier: PMID (pubmed), normalized DOI (crossref), nothing (file)
                    native_ids = {"pubmed": meta.get("pmid"), "crossref": clean_doi(meta["doi"])}
                    meta |= {
                        "source": source,
                        "native_id": native_ids.get(source),
                        "origin": e.get("origin") or "references",
                        "origin_seeds": e.get("seeds"),
                        "origin_ref": str(job.id),
                    }
                    inserted, has_text = await _insert_article(session, project.id, meta, client)
                    if inserted:
                        imported, fulltext = imported + 1, fulltext + has_text
                        sources[source] += 1
                        known |= keys(meta.get("pmid"), meta.get("doi"), meta.get("title"))
                    else:
                        duplicates += 1
            except Exception:
                logger.exception("reference import failed for %s", label(e))
                failed += 1
                failed_items.append(label(e))
            done += 1
            await progress()
    result = {
        "imported": imported,
        "failed": failed,
        "fulltext": fulltext,
        "total_count": len(refs),
        "truncated": truncated,
        "duplicates": duplicates,
        "sources": sources,
        "failed_items": failed_items[:200],
    }
    if finish is not None:
        result |= await finish()
    return await _finish_success(session, project, job, result)


async def _do_import_citations(
    session: AsyncSession, project: Project, job: ImportJob, opts: dict, client: httpx.AsyncClient
) -> dict:
    """Citation chaining: references / citing papers of the seed articles → corpus.

    ``opts``: seeds (PMID), direction, min_overlap, max_candidates, search_id (search whose
    question is reused). The candidates most linked to the seeds enter first; a search
    is created on the new articles, ready to be analyzed with the model.
    """
    seeds = [str(p) for p in opts["seeds"]]

    async def linking(done: int, total: int) -> None:
        job.result_json = {"phase": "linking", "done": done, "total": total}
        await session.commit()

    links = await fetch_links(seeds, client, opts.get("direction", "both"), linking)
    known = {
        p
        for p in (
            await session.execute(select(Article.pmid).where(Article.project_id == project.id))
        ).scalars()
        if p
    }
    ranked = rank(links, known, opts.get("min_overlap", 1), opts.get("max_candidates", 300))
    refs = [{**_entry(pmid=pmid), "origin": "citation", "seeds": n} for pmid, n in ranked]

    async def derive() -> dict:
        ids = list(
            (await session.execute(select(Article.id).where(Article.origin_ref == str(job.id))))
            .scalars()
            .all()
        )
        if not ids:
            return {"candidates": len(links), "search_id": None}
        origin = (
            await session.get(Search, uuid.UUID(opts["search_id"]))
            if opts.get("search_id")
            else None
        ) or await default_search(session, project)
        search = Search(
            project_id=project.id,
            name=await next_search_name(session, project.id),
            question=origin.question,
            fields=origin.fields,
            text_sections=origin.text_sections,
            read_windows=origin.read_windows,
            definition_hash=definition_hash(origin.question, origin.fields),
            lot_all=False,
            parent_spec={
                "citations": {
                    "direction": opts.get("direction", "both"),
                    "seeds": len(seeds),
                    "min_overlap": opts.get("min_overlap", 1),
                    "from": origin.name,
                }
            },
        )
        session.add(search)
        await session.flush()
        session.add_all(SearchArticle(search_id=search.id, article_id=i) for i in ids)
        await session.commit()
        return {"candidates": len(links), "search_id": str(search.id)}

    if not refs:
        return await _finish_success(
            session,
            project,
            job,
            {"imported": 0, "failed": 0, "fulltext": 0, "total_count": 0, "truncated": False}
            | {"candidates": len(links), "search_id": None},
        )
    return await _do_import_refs(session, project, job, refs, client, finish=derive)


async def _file_meta(e: dict, email: str, client: httpx.AsyncClient) -> tuple[dict | None, str]:
    """Non-PubMed metadata: Crossref (DOI) completed by the file; else the file alone."""
    cr = await crossref(e["doi"], email, client) if e["doi"] else None
    src = cr or e
    if cr is None and e["doi"] and not e["title"]:
        return None, "crossref"  # unknown DOI and nothing else to store
    if not (src.get("title") or e.get("title")):
        return None, "crossref"
    year = src.get("year") or e.get("year")
    return {
        "pmid": None,
        "doi": e["doi"],
        "title": src.get("title") or e.get("title"),
        "abstract": src.get("abstract") or e.get("abstract"),
        "journal": src.get("journal") or e.get("journal"),
        "pub_date": str(year) if year else None,
        "authors": src.get("authors") or e.get("authors"),
        "language": src.get("language") or e.get("language"),
    }, "crossref" if cr else "file"


async def _fulltext_for(
    meta: dict, adapter: SourceAdapter | None, client: httpx.AsyncClient
) -> tuple[str | None, list[dict], str, str | None, str | None] | None:
    """Full text of the record → ``(methods, sections, source, url, note)`` or ``None``.

    Full-text id: ``fulltext_id`` (non-PubMed source, e.g. Europe PMC: JATS, label and
    URL of the record, no heuristic) else ``pmcid`` (PubMed: non-archivable-preprint
    heuristic + PMC URL). ``adapter=None`` (default refs/citations path): only the
    ``pmcid`` path works (module-level ``fetch_fulltext`` function)."""
    fulltext_id = meta.get("fulltext_id") or meta.get("pmcid")
    if not fulltext_id:
        return None
    try:
        if meta.get("fulltext_id"):
            result = await adapter.fetch_fulltext(fulltext_id) if adapter is not None else None
            if result is None:
                return None  # non disponible (pas de fulltext_id utilisable)
            methods, sections = result
            return methods, sections, meta.get("fulltext_source"), meta.get("fulltext_url"), None
        if adapter is not None:
            result = await adapter.fetch_fulltext(fulltext_id)
            methods, sections = (None, []) if result is None else result
        else:
            methods, sections = await fetch_fulltext(fulltext_id, client)
        # Non-archivable preprint in PMC: the "body" is just a 2-line notice.
        # Too short to be an article: left to completion (other sources).
        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{fulltext_id}/"
        if long_enough(sections):
            return methods, sections, "pmc", pmc_url, None
        if pmc_not_archived(sections):
            return None, [], "pmc", pmc_url, "preprint_not_archived"
    except Exception:
        logger.exception("fetch_fulltext failed for %s", fulltext_id)
    return None


def _apply_fulltext(target: dict | Article, ft: tuple) -> None:
    """Sets the full text ``(methods, sections, source, url, note)`` on a record
    (``values``, dict) or an existing row (``Article``, DOI attachment)."""
    methods, sections, source, url, note = ft

    def set_(key: str, value) -> None:
        if isinstance(target, dict):
            target[key] = value
        else:
            setattr(target, key, value)

    if sections:
        set_("methods_text", methods)
        set_("sections", sections)
    set_("fulltext_source", source)
    if url:
        set_("fulltext_url", url)
    if note:
        set_("fulltext_note", note)


async def _insert_article(
    session: AsyncSession,
    project_id: uuid.UUID,
    meta: dict,
    client: httpx.AsyncClient,
    adapter: SourceAdapter | None = None,
) -> tuple[bool, bool]:
    """INSERT ... ON CONFLICT DO NOTHING (M2) → (inserted, full text present).

    No conflict target: any uniqueness violation (PMID or source/native_id triplet) is
    ignored. Phase 1: the full text goes through ``adapter`` if injected, else the
    module-level ``fetch_fulltext`` function (default path: refs, citations,
    existing tests).
    """
    source = meta.get("source") or ("pubmed" if meta.get("pmid") else None)
    values = {
        "project_id": project_id,
        "source": source,
        "native_id": meta.get("native_id") or (meta.get("pmid") if source == "pubmed" else None),
        "pmid": meta.get("pmid"),
        "pmcid": meta.get("pmcid"),
        "doi": meta.get("doi"),
        "title": meta.get("title"),
        "abstract": meta.get("abstract"),
        **_biblio(meta),
        "original_language": _map_language(meta.get("language")),
        "origin": meta.get("origin"),
        "origin_seeds": meta.get("origin_seeds"),
        "origin_ref": meta.get("origin_ref"),
    }
    if ft := await _fulltext_for(meta, adapter, client):
        _apply_fulltext(values, ft)
    elif note := meta.get("fulltext_note"):
        # metadata-only source (OpenAlex): explicit note, no full text
        values["fulltext_note"] = note
    insert = _pg_insert if session.get_bind().dialect.name == "postgresql" else _sqlite_insert
    stmt = insert(Article).values(**values).on_conflict_do_nothing()
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0, bool(values.get("sections"))


async def _link_doi_article(
    session: AsyncSession,
    project_id: uuid.UUID,
    meta: dict,
    owners: set,
    client: httpx.AsyncClient,
    adapter: SourceAdapter | None,
) -> None:
    """DOI attachment (archi § 3): the incoming record fills the EMPTY fields of the
    existing row (first by ``Article.id`` among the identities carrying the DOI).

    An existing non-empty value is never overwritten; the identity of the existing row
    (``source``/``native_id``/``origin``/``origin_ref``/``origin_seeds``) is intact;
    the full text is sought only if the row has none."""
    conds = [and_(Article.source == s, Article.native_id == n) for s, n in owners]
    row = (
        await session.execute(
            select(Article)
            .where(Article.project_id == project_id, or_(*conds))
            .order_by(Article.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    bib = _biblio(meta)
    bib.pop("pub_types")  # spec D3: completed fields, no pub_types
    fills = {
        "pmid": meta.get("pmid"),
        "pmcid": meta.get("pmcid"),
        "title": meta.get("title"),
        "abstract": meta.get("abstract"),
        **bib,
        "original_language": _map_language(meta.get("language")),
        "fulltext_note": meta.get("fulltext_note"),  # D2: set if empty, never overridden
    }
    for col, val in fills.items():
        if val and not getattr(row, col):
            setattr(row, col, val)
    if not row.sections and (ft := await _fulltext_for(meta, adapter, client)):
        _apply_fulltext(row, ft)


async def _finish_success(
    session: AsyncSession, project: Project, job: ImportJob, result: dict
) -> dict:
    """Success (m10): project FROZEN + job COMPLETED (result_json)."""
    project.status = ProjectStatus.FROZEN
    job.status = ImportJobStatus.COMPLETED
    job.result_json = result
    job.completed_at = datetime.now(UTC)
    await session.commit()
    return result


async def _record_failure(
    session: AsyncSession,
    project: Project,
    job: ImportJob,
    exc: Exception,
    restore: ProjectStatus = ProjectStatus.DRAFT,
) -> None:
    """Failure (m10): job FAILED (sanitized error, C-1) + project becomes DRAFT again — best effort.

    C-1: ``job.error`` and the server log use ``_sanitize_error`` (no raw
    traceback with the URL or the NCBI key).
    """
    error = _sanitize_error(exc)
    logger.error("import failed (project %s): %s", project.id, error)
    try:
        await session.rollback()
        job.status = ImportJobStatus.FAILED
        job.error = error
        job.completed_at = datetime.now(UTC)
        project.status = restore
        await session.commit()
    except Exception:
        logger.exception("failed to record import job failure")


# --- Full text completion (open sources) -------------------------------------


async def backfill_metadata(session: AsyncSession, project_id: uuid.UUID, client) -> int:
    """Journal / date / authors of the articles before their import (efetch)."""
    arts = [
        a
        for a in (await session.execute(select(Article).where(Article.project_id == project_id)))
        .scalars()
        .all()
        if a.pmid and a.pub_date is None and a.journal is None
    ]
    api_key = await get_setting(session, "ncbi_api_key")
    done = 0
    for batch in _chunks(arts, _EFETCH_BATCH):
        try:
            metas = {
                m["pmid"]: m for m in await fetch_metadata([a.pmid for a in batch], api_key, client)
            }
        except Exception:
            logger.exception("metadata backfill batch failed")
            continue
        for a in batch:
            if a.pmid in metas:
                for k, v in _biblio(metas[a.pmid]).items():
                    setattr(a, k, v)
                done += 1
        await session.commit()
    return done


@celery_app.task(name="diggypaper.complete_fulltext", queue=INGEST_QUEUE)
def complete_fulltext_task(project_id: str) -> dict:
    """Sync Celery wrapper (same schema as the import): dedicated thread + asyncio.run."""
    job_id = current_task.request.id
    out: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal out, error
        try:
            out = asyncio.run(_complete_fulltext(project_id, job_id=job_id))
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=_run, name="complete-fulltext")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return out


async def _complete_fulltext(
    project_id: str, session_factory=None, http: httpx.AsyncClient | None = None, job_id=None
) -> dict:
    """Seeks (open sources) the full text of the articles that do not have one."""
    session_factory = session_factory or _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    client = http if http is not None else httpx.AsyncClient(timeout=45.0)
    try:
        async with factory() as session:
            project = await session.get(Project, uuid.UUID(project_id))
            if project is None:
                raise LookupError(f"project not found: {project_id}")
            email = (
                await get_setting(session, "contact_email")
                or await get_setting(session, "smtp_from_email")
                or "diggypaper@example.org"
            )
            job = ImportJob(
                project_id=project.id,
                query="fulltext",
                kind="fulltext",
                job_id=job_id,
                status=ImportJobStatus.RUNNING,
                result_json={"phase": "fulltext", "done": 0, "total": 0, "found": 0},
            )
            session.add(job)
            await session.commit()
            await backfill_metadata(session, project.id, client)
            todo = [
                a
                for a in (
                    await session.execute(select(Article).where(Article.project_id == project.id))
                )
                .scalars()
                .all()
                if not long_enough(a.sections or [])
            ]
            found, sources = 0, {}

            def state(phase: str) -> dict:
                return {
                    "phase": phase,
                    "done": done,
                    "total": len(todo),
                    "found": found,
                    "sources": sources,
                }

            done = 0
            job.result_json = state("fulltext")
            await session.commit()
            try:
                for art in todo:
                    pmid = art.pmid  # a rollback of a replacement can expire the ORM object
                    try:
                        source = await complete_article(session, art, email, client)
                    except Exception:
                        logger.exception("open fulltext failed for pmid %s", pmid)
                        source = None
                    if source:
                        found += 1
                        key = source.split(":")[0]
                        sources[key] = sources.get(key, 0) + 1
                    done += 1
                    job.result_json = state("fulltext")
                    await session.commit()
            except Exception as exc:
                await session.rollback()
                job.status = ImportJobStatus.FAILED
                job.error = _sanitize_error(exc)
                job.completed_at = datetime.now(UTC)
                await session.commit()
                raise
            job.status = ImportJobStatus.COMPLETED
            job.result_json = state("done")
            job.completed_at = datetime.now(UTC)
            await session.commit()
            return job.result_json
    finally:
        if http is None:
            await client.aclose()
        if engine is not None:
            await engine.dispose()
