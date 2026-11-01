"""Tests for the PubMed import task (app/tasks) — mocked NCBI, injected session_factory.

C1/C2: the Celery wrapper (sync) runs the async core in a dedicated thread via
``asyncio.run``; the core creates its own engine per run (or uses the injected
``session_factory`` / the ``_session_factory_override`` hook in tests)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Article, ImportJob, ImportJobStatus, Project, ProjectStatus
from app.pubmed import NCBIError
from app.settings.service import set_setting
from app.tasks import _import_pubmed, _map_language, _sanitize_error, import_pubmed_task


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No real sleep in tests (NCBI throttle + backoff retry)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


def _efetch_xml(pmids: list[str], with_pmcid: bool = True) -> str:
    """efetch XML: one PubmedArticle per pmid; pmcid on the even ones if with_pmcid."""
    articles = []
    for i, pmid in enumerate(pmids):
        pmcid = f"PMC{pmid}" if (with_pmcid and i % 2 == 0) else None
        pmcid_tag = f'<ArticleId IdType="pmc">{pmcid}</ArticleId>' if pmcid else ""
        articles.append(
            f"""<PubmedArticle>
  <MedlineCitation>
    <PMID>{pmid}</PMID>
    <Article>
      <ArticleTitle>Title {pmid}</ArticleTitle>
      <Abstract><AbstractText>Abstract {pmid}.</AbstractText></Abstract>
      <Language>English</Language>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">{pmid}</ArticleId>
      {pmcid_tag}
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>"""
        )
    return f"<PubmedArticleSet>{''.join(articles)}</PubmedArticleSet>"


_LONG = "Outcome measured at twelve weeks. " * 60
_PMC_XML = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>Methods</title>
        <p>Randomised design.</p>
      </sec>
      <sec>
        <title>Results</title>
        <p>{LONG}</p>
      </sec>
    </body>
  </article>
</pmc-articleset>""".replace("{LONG}", _LONG)


def _ncbi_handler(pmids: list[str], efetch_pmids: list[str] | None = None, with_pmcid: bool = True):
    """Handler NCBI : esearch (count si retmax=0, idlist sinon) + efetch (db=pubmed/pmc)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": str(len(pmids))}})
            return httpx.Response(200, json={"esearchresult": {"idlist": pmids}})
        if path.endswith("efetch.fcgi"):
            # db=pmc -> full text (Methods); db=pubmed -> metadata.
            if request.url.params.get("db") == "pmc":
                return httpx.Response(200, text=_PMC_XML)
            return httpx.Response(200, text=_efetch_xml(efetch_pmids or pmids, with_pmcid))
        raise AssertionError(f"chemin inattendu : {path}")

    return handler


async def _make_project(engine) -> Project:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = Project(title="Test project")
        session.add(project)
        await session.commit()
        return project


async def _articles_for(engine, project_id) -> list[Article]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        return list(
            (await session.execute(select(Article).where(Article.project_id == project_id)))
            .scalars()
            .all()
        )


async def _project_status(engine, project_id) -> ProjectStatus:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project_id)
        return project.status


async def _jobs_for(engine, project_id) -> list[ImportJob]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        return list(
            (await session.execute(select(ImportJob).where(ImportJob.project_id == project_id)))
            .scalars()
            .all()
        )


# --- _import_pubmed (core async) ---


async def test_import_pubmed_inserts_articles_and_freezes(engine):
    """Inserts the articles (metadata + methods) and marks the project FROZEN."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    handler = _ncbi_handler(["111", "222"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result == {
        "imported": 2,
        "failed": 0,
        "total_count": 2,
        "fulltext": 1,
        "truncated": False,
    }
    articles = await _articles_for(engine, project.id)
    assert {a.pmid for a in articles} == {"111", "222"}
    by_pmid = {a.pmid: a for a in articles}
    assert by_pmid["111"].pmcid == "PMC111"
    assert by_pmid["111"].methods_text == "Randomised design."
    assert by_pmid["111"].fulltext_source == "pmc"  # counted "with full text"
    assert by_pmid["111"].title == "Title 111"
    assert by_pmid["111"].abstract == "Abstract 111."
    assert by_pmid["111"].original_language == "en"
    assert by_pmid["222"].pmcid is None
    assert by_pmid["222"].methods_text is None
    assert await _project_status(engine, project.id) == ProjectStatus.FROZEN


async def test_import_pubmed_count_zero_frozen(engine):
    """m10: count=0 -> success (empty corpus) -> FROZEN, 0 article, no efetch."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("esearch.fcgi")
        assert request.url.params["retmax"] == "0"
        return httpx.Response(200, json={"esearchresult": {"count": "0"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(
            str(project.id), "no-results-query", session_factory=factory, http=http
        )
    assert result == {
        "imported": 0,
        "failed": 0,
        "total_count": 0,
        "fulltext": 0,
        "truncated": False,
    }
    assert await _articles_for(engine, project.id) == []
    assert await _project_status(engine, project.id) == ProjectStatus.FROZEN


async def test_import_pubmed_partial_error_continues(engine):
    """A pmid missing from the efetch -> counted failed, the others imported, FROZEN."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    handler = _ncbi_handler(pmids=["111", "222"], efetch_pmids=["111"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result == {
        "imported": 1,
        "failed": 1,
        "total_count": 2,
        "fulltext": 1,
        "truncated": False,
    }
    articles = await _articles_for(engine, project.id)
    assert [a.pmid for a in articles] == ["111"]
    assert await _project_status(engine, project.id) == ProjectStatus.FROZEN


async def test_import_pubmed_unknown_project_raises(engine):
    """Nonexistent project -> total error (LookupError), nothing inserted, no job."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    handler = _ncbi_handler(["111"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(LookupError):
            await _import_pubmed(
                "00000000-0000-0000-0000-000000000000", "q", session_factory=factory, http=http
            )


async def test_import_pubmed_total_error_raises(engine):
    """Total error (NCBI down) -> raises NCBIError (C-1), job FAILED, project DRAFT (m10)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="NCBI down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(NCBIError):
            await _import_pubmed(
                str(project.id), "cancer", session_factory=factory, http=http, job_id="job-xyz"
            )
    assert await _articles_for(engine, project.id) == []
    assert await _project_status(engine, project.id) == ProjectStatus.DRAFT
    jobs = await _jobs_for(engine, project.id)
    assert len(jobs) == 1
    assert jobs[0].status == ImportJobStatus.FAILED
    assert jobs[0].error is not None
    assert jobs[0].result_json is None or jobs[0].result_json == {"phase": "searching"}
    assert jobs[0].completed_at is not None


async def test_import_failure_error_sanitized_no_key_no_url(engine):
    """C-1: NCBI 503 + key defined -> job.error without key nor URL (clean NCBIError)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await set_setting(session, "ncbi_api_key", "SECRET-NCBI-KEY-456")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(NCBIError):
            await _import_pubmed(
                str(project.id), "cancer", session_factory=factory, http=http, job_id="job-err"
            )
    jobs = await _jobs_for(engine, project.id)
    assert len(jobs) == 1
    assert jobs[0].status == ImportJobStatus.FAILED
    assert jobs[0].error is not None
    assert "SECRET-NCBI-KEY-456" not in jobs[0].error
    assert "eutils" not in jobs[0].error
    assert "api_key" not in jobs[0].error


# --- M3: job persistence (import_jobs) ---


async def test_import_creates_import_job(engine):
    """M3: the job is persisted (PENDING -> COMPLETED, query + result_json)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    handler = _ncbi_handler(["111", "222"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await _import_pubmed(
            str(project.id), "cancer AND mice", session_factory=factory, http=http, job_id="job-abc"
        )
    jobs = await _jobs_for(engine, project.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.project_id == project.id
    assert job.query == "cancer AND mice"
    assert job.job_id == "job-abc"
    assert job.status == ImportJobStatus.COMPLETED
    assert job.result_json == {
        "imported": 2,
        "failed": 0,
        "total_count": 2,
        "fulltext": 1,
        "truncated": False,
    }
    assert job.error is None
    assert job.created_at is not None
    assert job.completed_at is not None


# --- M2: state guard + deduplication ---


async def test_import_processing_project_raises(engine):
    """Import already in progress (PROCESSING) -> refused (LookupError), no job, no article."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project.id)
        project.status = ProjectStatus.PROCESSING
        await session.commit()
    handler = _ncbi_handler(["111"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(LookupError):
            await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert await _jobs_for(engine, project.id) == []
    assert await _articles_for(engine, project.id) == []


async def test_import_completes_frozen_project(engine):
    """A FROZEN project accepts a new import that completes the corpus."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project.id)
        project.status = ProjectStatus.FROZEN
        await session.commit()
    handler = _ncbi_handler(["111"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result["imported"] == 1
    assert len(await _articles_for(engine, project.id)) == 1


async def test_reimport_no_duplicates(engine):
    """M2: re-import -> no duplicates (ON CONFLICT DO NOTHING)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    handler = _ncbi_handler(["111", "222"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r1 = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    # the project is FROZEN → set it back to DRAFT to re-import
    async with factory() as session:
        project = await session.get(Project, project.id)
        project.status = ProjectStatus.DRAFT
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r2 = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert r1 == {"imported": 2, "failed": 0, "total_count": 2, "fulltext": 1, "truncated": False}
    # re-import: everything is a duplicate -> 0 imported, 0 failure
    assert r2 == {"imported": 0, "failed": 0, "total_count": 2, "fulltext": 0, "truncated": False}
    articles = await _articles_for(engine, project.id)
    assert len(articles) == 2
    assert {a.pmid for a in articles} == {"111", "222"}


# --- M1 : troncature ---


async def test_truncation_count_over_10000(engine):
    """M1: count > 10000 -> truncated=True + total_count = real count (not len(pmids))."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    pmids = [str(1000 + i) for i in range(150)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "15000"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": pmids}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(200, text=_efetch_xml(pmids, with_pmcid=False))
        raise AssertionError(f"chemin inattendu : {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result == {
        "imported": 150,
        "failed": 0,
        "total_count": 15000,
        "fulltext": 0,
        "truncated": True,
    }
    jobs = await _jobs_for(engine, project.id)
    assert jobs[0].result_json["truncated"] is True


# --- I4 : chunking >100 pmids ---


async def test_chunking_over_100_pmids(engine):
    """I4: 150 pmids -> efetch in batches of 100 (100 + 50)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    pmids = [str(1000 + i) for i in range(150)]
    efetch_batches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "150"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": pmids}})
        if request.url.path.endswith("efetch.fcgi"):
            efetch_batches.append(request.url.params["id"])
            batch = request.url.params["id"].split(",")
            return httpx.Response(200, text=_efetch_xml(batch, with_pmcid=False))
        raise AssertionError(f"chemin inattendu : {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result["imported"] == 150
    assert len(efetch_batches) == 2
    assert efetch_batches[0].split(",") == pmids[:100]
    assert efetch_batches[1].split(",") == pmids[100:]


# --- Bornes date (date_from / date_to) ---


async def test_import_pubmed_count_term_bounded_by_dates(engine):
    """The esearch count (retmax=0) receives the bounded [dp] term; total_count = bounded count."""
    from datetime import date

    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                term = request.url.params["term"]
                assert '"2021/01/01"[dp]' in term and '"2021/12/31"[dp]' in term
                return httpx.Response(200, json={"esearchresult": {"count": "2"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": ["111", "222"]}})
        return httpx.Response(200, text=_efetch_xml(["111", "222"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(
            str(project.id),
            "cancer",
            session_factory=factory,
            http=http,
            date_from=date(2021, 1, 1),
            date_to=date(2021, 12, 31),
        )
    assert result["imported"] == 2
    assert result["total_count"] == 2


async def test_import_pubmed_without_dates_count_term_unchanged(engine):
    """No bounds: the count term is the raw query (no [dp] added)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                term = request.url.params["term"]
                assert term == "cancer" and "[dp]" not in term
                return httpx.Response(200, json={"esearchresult": {"count": "1"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": ["111"]}})
        return httpx.Response(200, text=_efetch_xml(["111"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    assert result["imported"] == 1
    assert result["total_count"] == 1


def test_import_pubmed_task_wrapper_passes_dates():
    """The sync wrapper propagates the date bounds to the async core."""
    from datetime import date

    with patch("app.tasks._import_pubmed", new_callable=AsyncMock) as mock_core:
        mock_core.return_value = {
            "imported": 0,
            "failed": 0,
            "total_count": 0,
            "fulltext": 0,
            "truncated": False,
        }
        import_pubmed_task("proj-id", "q", date_from=date(2024, 1, 1), date_to=date(2024, 6, 30))
    assert mock_core.await_args.kwargs["date_from"] == date(2024, 1, 1)
    assert mock_core.await_args.kwargs["date_to"] == date(2024, 6, 30)


# --- C-1: error sanitization (job.error + server log) ---


def test_sanitize_error_ncbi_error_passthrough():
    """C-1: NCBIError -> its message (already clean, no URL nor key)."""
    assert _sanitize_error(NCBIError("HTTP 503")) == "HTTP 503"
    assert _sanitize_error(NCBIError("timeout")) == "timeout"


def test_sanitize_error_filters_api_key():
    """C-1: other exception -> api_key=… masked (no key leak)."""
    exc = ValueError(
        "Server error '503' for url 'https://eutils.ncbi.nlm.nih.gov/esearch.fcgi"
        "?db=pubmed&api_key=SECRET123'"
    )
    result = _sanitize_error(exc)
    assert "SECRET123" not in result
    assert "api_key=***" in result


def test_sanitize_error_truncates_500():
    """C-1: message over 500 characters -> truncated to 500."""
    assert len(_sanitize_error(ValueError("x" * 600))) == 500


def test_sanitize_error_empty_falls_back_to_type():
    """C-1: empty str(exc) -> the type name (no empty string)."""
    assert _sanitize_error(ValueError("")) == "ValueError"


# --- m3 : mapping langue ---


def test_map_language_unknown_keeps_raw():
    """m3: language outside en/fr/de/es -> raw label (no forced 'en')."""
    assert _map_language("English") == "en"
    assert _map_language("french") == "fr"
    assert _map_language("German") == "de"
    assert _map_language("Spanish") == "es"
    assert _map_language("Italian") == "Italian"
    assert _map_language("  Italian  ") == "Italian"
    assert _map_language(None) == "en"


async def test_import_unknown_language_stored_raw(engine):
    """m3: an Italian article keeps 'Italian' (free VARCHAR, no enum)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    xml = """<PubmedArticleSet><PubmedArticle>
  <MedlineCitation>
    <PMID>333</PMID>
    <Article>
      <ArticleTitle>T</ArticleTitle>
      <Language>Italian</Language>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">333</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle></PubmedArticleSet>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "1"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": ["333"]}})
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await _import_pubmed(str(project.id), "cancer", session_factory=factory, http=http)
    articles = await _articles_for(engine, project.id)
    assert articles[0].original_language == "Italian"


# --- C1/C2: thread + engine-per-run pattern ---


async def test_import_creates_own_engine(tmp_path, monkeypatch):
    """C2: without session_factory, the core creates its own engine (DATABASE_URL,
    read at call time) and disposes it at the end of the run (no loop reuse)."""
    db_file = tmp_path / "own_engine.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = Project(title="P")
        session.add(project)
        await session.commit()
        project_id = project.id
    await eng.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    handler = _ncbi_handler(["111"])
    real_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.tasks.httpx.AsyncClient", _client_factory)
    result = await _import_pubmed(str(project_id), "cancer")
    assert result == {
        "imported": 1,
        "failed": 0,
        "total_count": 1,
        "fulltext": 1,
        "truncated": False,
    }
    # verification via a new engine (the run disposed its own)
    eng2 = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory2 = async_sessionmaker(eng2, class_=AsyncSession, expire_on_commit=False)
    async with factory2() as session:
        articles = list((await session.execute(select(Article))).scalars().all())
        project = await session.get(Project, project_id)
    await eng2.dispose()
    assert [a.pmid for a in articles] == ["111"]
    assert project.status == ProjectStatus.FROZEN


async def test_import_real_path_eager_no_delay_mock(engine, monkeypatch):
    """C1/C2: real path in eager mode, WITHOUT .delay or core mocks.

    Called from a running asyncio loop (like in a FastAPI request in eager
    mode): the sync wrapper spawns a dedicated thread that does
    ``asyncio.run`` — no RuntimeError 'cannot be called from a running
    event loop', no reuse of an engine bound to a closed loop."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks._session_factory_override", factory)
    handler = _ncbi_handler(["111", "222"])
    real_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.tasks.httpx.AsyncClient", _client_factory)

    result = import_pubmed_task.delay(str(project.id), "cancer")

    # eager: the job is already done (no PENDING)
    assert result.state == "SUCCESS"
    assert result.result == {
        "imported": 2,
        "failed": 0,
        "total_count": 2,
        "fulltext": 1,
        "truncated": False,
    }
    articles = await _articles_for(engine, project.id)
    assert {a.pmid for a in articles} == {"111", "222"}
    assert await _project_status(engine, project.id) == ProjectStatus.FROZEN
    jobs = await _jobs_for(engine, project.id)
    assert len(jobs) == 1
    assert jobs[0].status == ImportJobStatus.COMPLETED
    assert jobs[0].job_id is not None  # the Celery id is persisted


# --- import_pubmed_task (wrapper sync) ---


def test_import_pubmed_task_wrapper_calls_core():
    """The sync wrapper calls the async core (mocked) with the Celery id of the job."""
    with patch("app.tasks._import_pubmed", new_callable=AsyncMock) as mock_core:
        mock_core.return_value = {
            "imported": 1,
            "failed": 0,
            "total_count": 1,
            "fulltext": 1,
            "truncated": False,
        }
        result = import_pubmed_task("proj-id", "query")
    assert mock_core.await_count == 1
    args, kwargs = mock_core.await_args
    assert args == ("proj-id", "query")
    assert "job_id" in kwargs
    assert result == {
        "imported": 1,
        "failed": 0,
        "total_count": 1,
        "fulltext": 1,
        "truncated": False,
    }


async def test_pmc_stub_body_is_not_kept_as_fulltext(engine, monkeypatch):
    """Non-archivable preprint: PMC returns only a 2-line notice, not a full text."""
    from app import tasks

    notice = [{"title": "Full Text Availability", "kind": "other", "text": "Not archived. " * 12}]

    async def stub(pmcid, client):
        return None, notice

    monkeypatch.setattr(tasks, "fetch_fulltext", stub)
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        inserted, has_text = await tasks._insert_article(
            session, project.id, {"pmid": "9", "pmcid": "PMC9", "title": "t"}, None
        )
        await session.commit()
    assert inserted and not has_text
    (art,) = await _articles_for(engine, project.id)
    assert not art.sections and art.fulltext_source is None
    assert art.fulltext_note is None  # simple texte court, pas l'avis de licence PMC


async def test_pmc_license_notice_sets_fulltext_note(engine, monkeypatch):
    from app import tasks

    notice = [
        {
            "title": "Full Text Availability",
            "kind": "other",
            "text": "The license terms selected by the author(s) for this preprint version do "
            "not permit archiving in PMC. The full text is available from the preprint server.",
        }
    ]

    async def stub(pmcid, client):
        return None, notice

    monkeypatch.setattr(tasks, "fetch_fulltext", stub)
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await tasks._insert_article(
            session, project.id, {"pmid": "7", "pmcid": "PMC7", "title": "t"}, None
        )
        await session.commit()
    (art,) = await _articles_for(engine, project.id)
    assert not art.sections and art.fulltext_note == "preprint_not_archived"
