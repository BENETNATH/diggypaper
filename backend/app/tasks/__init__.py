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
from datetime import UTC, datetime

import httpx
from celery import current_task
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    ImportJob,
    ImportJobStatus,
    Project,
    ProjectStatus,
)
from app.fulltext import complete_article, long_enough
from app.pubmed import (
    NCBIError,
    _esearch_max,
    fetch_fulltext,
    fetch_metadata,
    fetch_pmids,
    search_volume,
)
from app.settings.service import get_setting

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


@celery_app.task(name="diggypaper.import_pubmed")
def import_pubmed_task(project_id: str, query: str) -> dict:
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
            result = asyncio.run(_import_pubmed(project_id, query, job_id=job_id))
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
) -> dict:
    """Core async de l'import PubMed.

    - ``session_factory`` injectable (tests) ; sinon le core crée son propre
      engine par run (DATABASE_URL) et le dispose en fin (C2).
    - ``http`` injectable (tests) ; sinon ``httpx.AsyncClient(timeout=60.0)``
      (m4), fermé en ``finally``.
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
            job = ImportJob(project_id=project.id, query=query, job_id=job_id)
            session.add(job)
            await session.commit()  # PENDING (server_default)
            job.status = ImportJobStatus.RUNNING
            job.result_json = {"phase": "searching"}
            project.status = ProjectStatus.PROCESSING  # m10
            await session.commit()

            try:
                return await _do_import(session, project, job, query, client)
            except Exception as exc:
                await _record_failure(session, project, job, exc, restore=previous)
                raise
    finally:
        if owns_client:
            await client.aclose()
        if engine is not None:
            await engine.dispose()


async def _do_import(
    session: AsyncSession, project: Project, job: ImportJob, query: str, client: httpx.AsyncClient
) -> dict:
    """Corps de l'import : esearch → efetch par lots → insertion (m6 : commit par batch)."""
    api_key = await get_setting(session, "ncbi_api_key")
    # m11 : TOCTOU entre les deux esearch (count puis idlist) — acceptable,
    # l'import est un instantané ; ``total_count`` est le count réel.
    count = await search_volume(query, api_key, client)
    if count == 0:
        return await _finish_success(
            session,
            project,
            job,
            {"imported": 0, "failed": 0, "fulltext": 0, "total_count": 0, "truncated": False},
        )
    pmids = await fetch_pmids(query, api_key, client)
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
    done = 0

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
            metas = await fetch_metadata(batch, api_key, client)
        except Exception:
            logger.exception("efetch batch failed (%d pmids)", len(batch))
            failed += len(batch)
            done += len(batch)
            await progress()
            continue
        by_pmid = {m["pmid"]: m for m in metas if m.get("pmid")}
        for pmid in batch:
            meta = by_pmid.get(pmid)
            if meta is None:
                failed += 1
            else:
                try:
                    inserted, has_text = await _insert_article(session, project.id, meta, client)
                    if inserted:
                        imported += 1
                        fulltext += has_text
                    # sinon : doublon (re-import) → ni importé ni échec (M2)
                except Exception:
                    logger.exception("article insert failed for pmid %s", pmid)
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
        },
    )


async def _insert_article(
    session: AsyncSession, project_id: uuid.UUID, meta: dict, client: httpx.AsyncClient
) -> tuple[bool, bool]:
    """INSERT ... ON CONFLICT (project_id, pmid) DO NOTHING (M2) → (inséré, full text présent).

    ``index_where`` est requis pour que la cible de conflit corresponde à
    l'index unique partiel (``pmid IS NOT NULL``) — sur SQLite comme sur PG.
    """
    values = {
        "project_id": project_id,
        "pmid": meta["pmid"],
        "pmcid": meta.get("pmcid"),
        "doi": meta.get("doi"),
        "title": meta.get("title"),
        "abstract": meta.get("abstract"),
        **_biblio(meta),
        "original_language": _map_language(meta.get("language")),
    }
    if meta.get("pmcid"):
        try:
            methods, sections = await fetch_fulltext(meta["pmcid"], client)
            # Préprint non archivable dans PMC : le « corps » n'est qu'un avis de 2 lignes.
            # Trop court pour être un article : on le laisse à la complétion (autres sources).
            if long_enough(sections):
                values["methods_text"], values["sections"] = methods, sections
                values["fulltext_source"] = "pmc"
                values["fulltext_url"] = f"https://pmc.ncbi.nlm.nih.gov/articles/{meta['pmcid']}/"
        except Exception:
            logger.exception("fetch_fulltext failed for pmcid %s", meta["pmcid"])
    insert = _pg_insert if session.get_bind().dialect.name == "postgresql" else _sqlite_insert
    stmt = (
        insert(Article)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["project_id", "pmid"], index_where=text("pmid IS NOT NULL")
        )
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0, bool(values.get("sections"))


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


@celery_app.task(name="diggypaper.complete_fulltext")
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
