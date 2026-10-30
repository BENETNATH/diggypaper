"""Celery tasks : importation PubMed (Module 2).

Pattern d'exécution (C1/C2, validé empiriquement) :

- Le task Celery reste **sync** (prefork).
- Il exécute le core async (``_import_pubmed``) dans un **thread dédié** via
  ``asyncio.run`` : pas de boucle en cours dans ce thread → fonctionne en mode
  eager (boucle FastAPI en cours dans le thread appelant) comme en prod
  prefork (pas de réutilisation d'un engine asyncpg lié à une boucle fermée).
- Le core crée son **propre engine par run** (depuis ``DATABASE_URL``, lu à
  l'appel) et le **dispose** en fin de run. Si ``session_factory`` est injecté
  (tests), il ne crée pas d'engine.
- Le client httpx est créé par le core (timeout explicite, m4) et fermé en
  ``finally``.
- Les exceptions du thread sont propagées au task (le job Celery passe en
  FAILED ; le job import_jobs est marqué FAILED, le projet redevient DRAFT).

Sémantique des statuts projet (m10) : import démarre → PROCESSING ; succès
(y compris corpus vide) → FROZEN ; échec → DRAFT. ``COMPLETED`` est réservé au
MODULE 4 (inférence terminée).

TOCTOU (m11) : entre les deux appels esearch (``search_volume`` puis
``fetch_pmids``), le corpus PubMed peut changer — acceptable : l'import est un
instantané, le count est indicatif (le résultat persiste ``total_count`` réel).
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

#: Langue PubMed (libellé) → code ISO.
#: m3 : une langue hors en/fr/de/es garde son libellé brut (pas de forçage
#: 'en') — la colonne ``articles.original_language`` est un VARCHAR libre.
_LANGUAGE_MAP = {
    "english": "en",
    "french": "fr",
    "german": "de",
    "spanish": "es",
}

#: Registre des sources de recherche : code → fabrique ``(client, api_key) -> SourceAdapter``.
ADAPTERS = {"pubmed": PubMedAdapter, "europepmc": EuropePMCAdapter, "openalex": OpenAlexAdapter}

#: Setting de clé API par source (les autres sources n'en utilisent pas).
_SOURCE_KEY_SETTING = {"openalex": "openalex_api_key"}


def _source_key_setting(source: str) -> str:
    return _SOURCE_KEY_SETTING.get(source, "ncbi_api_key")


#: Taille des lots efetch (pmids par requête).
_EFETCH_BATCH = 100

#: Hook de test : si défini, le core l'utilise au lieu de créer son propre
#: engine depuis DATABASE_URL (le wrapper Celery n'a pas de paramètre de
#: session — le hook permet d'exercer le vrai chemin thread+asyncio.run en
#: tests avec l'engine SQLite de test).
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

#: C-1 : masque la clé NCBI dans les messages d'erreur (job.error + log serveur)
#: — ``api_key=…`` dans l'URL httpx (défense en profondeur : NCBIError est déjà
#: propre, ça couvre les autres exceptions).
_API_KEY_RE = re.compile(r"api_key=[^&\s'\"<>]*")


def _sanitize_error(exc: BaseException) -> str:
    """C-1 : message d'erreur propre pour ``job.error`` + log serveur.

    - ``NCBIError`` → son message (déjà sans URL ni clé).
    - sinon → ``str(exc)`` filtré (``api_key=…`` masqué par regex), tronqué à
      500 caractères (pas de traceback brut avec l'URL).
    """
    if isinstance(exc, NCBIError):
        return str(exc) or type(exc).__name__
    message = _API_KEY_RE.sub("api_key=***", str(exc))
    return message[:500] or type(exc).__name__


def _map_language(language: str | None) -> str:
    """Code ISO si connu, sinon le libellé PubMed brut (m3) ; ``en`` si absent."""
    if not language:
        return "en"
    label = language.strip()
    return _LANGUAGE_MAP.get(label.lower(), label)


def _biblio(meta: dict) -> dict:
    """Métadonnées bibliographiques d'un article PubMed (journal, date, auteurs, types)."""
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
    """Engine par run (C2) : créé depuis DATABASE_URL (env, lu à l'appel).

    Le run dispose l'engine en fin (``_import_pubmed``) : pas de réutilisation
    d'un engine lié à une boucle d'événements fermée.
    """
    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


#: File Celery des tâches qui parlent à NCBI : un seul worker la consomme, avec
#: ``--concurrency=1`` — le throttle de ``app.pubmed`` est par process (3 req/s sans clé,
#: 10 avec), donc plusieurs process le multiplieraient au-delà du plafond NCBI.
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
    """Wrapper sync Celery : exécute le core async dans un thread dédié (C1/C2).

    ``asyncio.run`` tourne dans le thread worker (pas de boucle en cours dans
    ce thread) : OK en mode eager (boucle FastAPI en cours dans le thread
    appelant) comme en prod prefork. Les exceptions du thread sont relevées
    dans le thread principal (le job Celery passe en FAILED).

    ``current_task`` est thread-local (stack Celery) : l'id du job est lu dans
    le thread appelant, avant de spawn le thread worker.
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
        except BaseException as exc:  # propagée au thread principal
            error = exc

    thread = threading.Thread(target=_run, name="import-pubmed")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    # Texte intégral manquant (hors PMC) : enchaîné automatiquement, en tâche séparée.
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
    """Core async de l'import PubMed (ou, avec ``refs``, d'une liste de références).

    - ``session_factory`` injectable (tests) ; sinon le core crée son propre
      engine par run (DATABASE_URL) et le dispose en fin (C2).
    - ``http`` injectable (tests) ; sinon ``httpx.AsyncClient(timeout=60.0)``
      (m4), fermé en ``finally``.
    - ``adapter`` (Phase 1) : contrat ``SourceAdapter`` ; ``None`` → celui de ``source`` dans
      ``ADAPTERS`` (construit dans ``_do_import``). Non utilisé par les chemins refs/citations.
    - ``job_id`` : id Celery du task (persisté dans import_jobs, M3).
    - Sémantique des statuts projet (m10) : démarrage → PROCESSING ; succès
      (y compris corpus vide) → FROZEN ; échec → DRAFT.
    - Erreur partielle (un pmid échoue) → log, continuer, ne pas bloquer.
    - Renvoie ``{"imported", "failed", "total_count", "truncated"}`` (M1 :
      ``total_count`` = count réel NCBI, pas ``len(pmids)``).
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
            # M2 : garde d'état — seul un projet DRAFT peut être importé
            # (l'API renvoie 409 ; c'est la garde TOCTOU côté task).
            if project.status == ProjectStatus.PROCESSING:
                raise LookupError(f"project {project_id} is already PROCESSING")
            # Un import peut compléter un corpus existant : en cas d'échec on y revient.
            previous = project.status

            # M3 : le job est persisté (query + statut + résultat/erreur).
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
    """Corps de l'import : esearch → efetch par lots → insertion (m6 : commit par batch)."""
    api_key = await get_setting(session, _source_key_setting(source))
    if adapter is None:
        if source not in ADAPTERS:
            raise LookupError("unknown source")
        adapter = ADAPTERS[source](client, api_key)
    bounds = DateBounds(date_from, date_to)
    # m11 : TOCTOU entre les deux esearch (count puis idlist) — acceptable,
    # l'import est un instantané ; ``total_count`` est le count réel.
    count = await adapter.search_volume(query, bounds)
    if count == 0:
        return await _finish_success(
            session,
            project,
            job,
            {"imported": 0, "failed": 0, "fulltext": 0, "total_count": 0, "truncated": False},
        )
    # Cap absent → le défaut de l'adapter (``_esearch_max()``), comme avant.
    pmids = [nid async for nid in adapter.iter_ids(query, bounds)]
    truncated = count > _esearch_max()
    if truncated:
        logger.warning(
            "import truncated: count=%d > %d (project %s) — seuls les %d premiers articles "
            "sont importés",
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
    # DOIs portés par des notices de CE import : un second DOI identique est sauté (pas
    # rattaché — le rattachement n'a de cible que des lignes existantes).
    import_dois: set[str] = set()
    # DOI normalisé → identités (source, native_id) déjà dans le projet : un DOI porté par
    # une autre identité EXISTANTE est rattaché (archi § 3), pas ré-inséré.
    doi_owners: dict[str, set] = {}
    for doi, src, nid in (
        await session.execute(
            select(Article.doi, Article.source, Article.native_id).where(
                Article.project_id == project.id, Article.doi.is_not(None)
            )
        )
    ).all():
        if key := clean_doi(doi):
            doi_owners.setdefault(key, set()).add((src, nid))

    async def progress() -> None:
        # Lisible par GET /jobs pendant le RUNNING (barre de l'écran d'import).
        job.result_json = {
            "phase": "importing",
            "done": done,
            "total": len(pmids),
            "imported": imported,
            "failed": failed,
            "fulltext": fulltext,
            "total_count": count,
        }
        await session.commit()  # m6 : commit fréquent (résilience partielle)

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
                    if others:
                        # DOI porté par une autre identité existante (archi § 3) : rattachement à
                        # la ligne (champs vides + plein texte si absent) — pas de doublon.
                        await _link_doi_article(session, project.id, meta, others, client, adapter)
                        duplicates_doi += 1
                    elif doi and doi in import_dois:
                        # Une notice de ce même import porte déjà le DOI : saut (pas de
                        # plein texte pour rien) — comportement phase 2.
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
                        # sinon : doublon (re-import) → ni importé ni échec (M2)
                except Exception:
                    logger.exception("article insert failed for %s %s", source, native_id)
                    failed += 1
            done += 1
            await progress()  # à chaque article : le full text PMC prend ~0,3 s/article
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
    """Liste de références (DOI, PMID, titres…) → articles du corpus.

    ``finish`` (coroutine, optionnelle) : appelée avant la clôture du job, son résultat (dict) est
    ajouté au résultat (ex. recherche créée sur les nouveaux articles).

    Résolution : PMID connu (ou trouvé via Europe PMC par DOI / titre exact) → notice PubMed
    complète ; sinon Crossref (DOI) ; sinon les métadonnées du fichier. Le texte intégral est
    cherché ensuite (``complete_fulltext_task``), comme après un import PubMed.
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
    # Déjà dans le corpus (re-import) : ignoré, comme les doublons PubMed.
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
                    # identifiant natif : PMID (pubmed), DOI normalisé (crossref), rien (file)
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
    """Chaînage de citations : références / citants des articles de départ → corpus.

    ``opts`` : seeds (PMID), direction, min_overlap, max_candidates, search_id (recherche dont la
    question est reprise). Les candidats les plus liés aux graines entrent d'abord ; une recherche
    est créée sur les nouveaux articles, prête à être analysée avec le modèle.
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
    """Métadonnées hors PubMed : Crossref (DOI) complété par le fichier ; sinon le fichier seul."""
    cr = await crossref(e["doi"], email, client) if e["doi"] else None
    src = cr or e
    if cr is None and e["doi"] and not e["title"]:
        return None, "crossref"  # DOI inconnu et rien d'autre à enregistrer
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
    """Plein texte de la notice → ``(methods, sections, source, url, note)`` ou ``None``.

    Id du plein texte : ``fulltext_id`` (source non-PubMed, ex. Europe PMC : JATS, label et
    URL de la notice, pas d'heuristique) sinon ``pmcid`` (PubMed : heuristique préprint non
    archivable + URL PMC). ``adapter=None`` (chemin défaut refs/citations) : seul le chemin
    ``pmcid`` fonctionne (fonction module-level ``fetch_fulltext``)."""
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
        # Préprint non archivable dans PMC : le « corps » n'est qu'un avis de 2 lignes.
        # Trop court pour être un article : on le laisse à la complétion (autres sources).
        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{fulltext_id}/"
        if long_enough(sections):
            return methods, sections, "pmc", pmc_url, None
        if pmc_not_archived(sections):
            return None, [], "pmc", pmc_url, "preprint_not_archived"
    except Exception:
        logger.exception("fetch_fulltext failed for %s", fulltext_id)
    return None


def _apply_fulltext(target: dict | Article, ft: tuple) -> None:
    """Pose le plein texte ``(methods, sections, source, url, note)`` sur une notice
    (``values``, dict) ou une ligne existante (``Article``, rattachement DOI)."""
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
    """INSERT ... ON CONFLICT DO NOTHING (M2) → (inséré, full text présent).

    Sans cible de conflit : toute violation d'unicité (PMID ou triplet source/native_id) est
    ignorée. Phase 1 : le plein texte passe par ``adapter`` s'il est injecté, sinon la
    fonction module-level ``fetch_fulltext`` (chemin défaut : refs, citations,
    tests existants).
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
        # source métadonnées seulement (OpenAlex) : note explicite, pas de plein texte
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
    """Rattachement DOI (archi § 3) : la notice entrante complète les champs VIDES de la
    ligne existante (première en ``Article.id`` parmi les identités porteuses du DOI).

    Une valeur existante non vide n'est jamais écrasée ; l'identité de la ligne existante
    (``source``/``native_id``/``origin``/``origin_ref``/``origin_seeds``) est intacte ;
    le plein texte est cherché seulement si la ligne n'en a pas."""
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
    bib.pop("pub_types")  # spec D3 : champs complétés, pas pub_types
    fills = {
        "pmid": meta.get("pmid"),
        "pmcid": meta.get("pmcid"),
        "title": meta.get("title"),
        "abstract": meta.get("abstract"),
        **bib,
        "original_language": _map_language(meta.get("language")),
        "fulltext_note": meta.get("fulltext_note"),  # D2 : posé si vide, jamais écrasé
    }
    for col, val in fills.items():
        if val and not getattr(row, col):
            setattr(row, col, val)
    if not row.sections and (ft := await _fulltext_for(meta, adapter, client)):
        _apply_fulltext(row, ft)


async def _finish_success(
    session: AsyncSession, project: Project, job: ImportJob, result: dict
) -> dict:
    """Succès (m10) : projet FROZEN + job COMPLETED (result_json)."""
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
    """Échec (m10) : job FAILED (error assainie, C-1) + projet redevient DRAFT — best effort.

    C-1 : ``job.error`` et le log serveur utilisent ``_sanitize_error`` (pas de
    traceback brut avec l'URL ni la clé NCBI).
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


# --- Complément de texte intégral (sources ouvertes) -------------------------


async def backfill_metadata(session: AsyncSession, project_id: uuid.UUID, client) -> int:
    """Journal / date / auteurs des articles importés avant leur prise en charge (efetch)."""
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
    """Wrapper sync Celery (même schéma que l'import) : thread dédié + asyncio.run."""
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
    """Cherche (sources ouvertes) le texte intégral des articles qui n'en ont pas."""
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
                    try:
                        source = await complete_article(session, art, email, client)
                    except Exception:
                        logger.exception("open fulltext failed for pmid %s", art.pmid)
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
