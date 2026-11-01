"""Tests for the SourceAdapter contract (Phase 1, spec 2026-10-28) — 100% offline.

A/D: contract alone with ``FakeSourceAdapter`` (no NCBI, no real httpx);
A.1–A.4 drive the **real** ``_import_pubmed`` pipeline with the injected adapter.
B/C: the PubMed adapter (``PubMedAdapter``) via httpx.MockTransport, like
``test_pubmed.py``. D: signature conformance to the ``Protocol``."""

import dataclasses
import inspect
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, ImportJob, ImportJobStatus, Project, ProjectStatus
from app.pubmed import NCBIError, PubMedAdapter, fetch_all_pmids
from app.sources import DateBounds, SourceAdapter
from app.tasks import _import_pubmed


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No real sleep in tests (NCBI throttle + backoff retry)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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


async def _jobs_for(engine, project_id) -> list[ImportJob]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        return list(
            (await session.execute(select(ImportJob).where(ImportJob.project_id == project_id)))
            .scalars()
            .all()
        )


async def _project_status(engine, project_id) -> ProjectStatus:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project_id)
        return project.status


class FakeSourceAdapter:
    """Contract testable alone: no NCBI, no DB; records its calls."""

    def __init__(
        self,
        volume: int = 0,
        ids: list[str] | None = None,
        meta_by_id: dict[str, dict] | None = None,
        fulltext_by_id: dict[str, tuple[str | None, list[dict]]] | None = None,
        *,
        fail_search_volume: Exception | None = None,
    ) -> None:
        self.volume = volume
        self.ids = ids or []
        self.meta_by_id = meta_by_id or {}
        self.fulltext_by_id = fulltext_by_id or {}
        self.fail_search_volume = fail_search_volume
        self.calls: list[tuple] = []

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        self.calls.append(("search_volume", query, bornes))
        if self.fail_search_volume is not None:
            raise self.fail_search_volume
        return self.volume

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        self.calls.append(("iter_ids", query, bornes, limit))
        for nid in self.ids[: limit if limit is not None else None]:
            yield nid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        self.calls.append(("fetch_metadata", ids))
        if not ids:
            return []
        return [self.meta_by_id[nid] for nid in ids if nid in self.meta_by_id]

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        self.calls.append(("fetch_fulltext", id))
        return self.fulltext_by_id.get(id)


# --- A. Contract alone, with the fake adapter (no NCBI, no DB) -----------


async def test_contract_fake_drives_import_pipeline_full_flow(engine):
    """The fake drives the real pipeline: order search_volume → iter_ids →
    fetch_metadata (per batch) → fetch_fulltext (per article) + stats."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ok_sections = [
        {"title": "Methods", "kind": "methods", "text": "Outcome measured at twelve weeks. " * 60}
    ]
    notice = [
        {
            "title": "Full Text Availability",
            "kind": "other",
            "text": "The license terms selected by the author(s) for this preprint version do "
            "not permit archiving in PMC. The full text is available from the preprint server.",
        }
    ]
    fake = FakeSourceAdapter(
        volume=3,
        ids=["3", "2", "1"],
        meta_by_id={
            "3": {"native_id": "3", "pmid": "3", "title": "t3", "pmcid": "PMC3"},
            "2": {"native_id": "2", "pmid": "2", "title": "t2", "pmcid": "PMC2"},
            "1": {"native_id": "1", "pmid": "1", "title": "t1", "pmcid": "PMC1"},
        },
        fulltext_by_id={
            "PMC3": ("methods3", ok_sections),
            "PMC2": ("methods2", ok_sections),
            "PMC1": (None, notice),
        },
    )
    result = await _import_pubmed(str(project.id), "q", session_factory=factory, adapter=fake)
    # Invocation order: search_volume -> iter_ids -> fetch_metadata (one batch) ->
    # fetch_fulltext (per article, secondary identifier).
    kinds = [c[0] for c in fake.calls]
    assert kinds[:3] == ["search_volume", "iter_ids", "fetch_metadata"]
    assert kinds[3:] == ["fetch_fulltext"] * 3
    assert fake.calls[2][1] == ["3", "2", "1"]
    assert [c[1] for c in fake.calls[3:]] == ["PMC3", "PMC2", "PMC1"]
    # Stats
    assert result["imported"] == 3
    assert result["fulltext"] == 2
    assert result["failed"] == 0
    assert result["total_count"] == 3
    # Articles: fulltext (methods + sections) for 2; license notice for the 3rd.
    articles = await _articles_for(engine, project.id)
    by_pmid = {a.pmid: a for a in articles}
    assert by_pmid["3"].methods_text == "methods3" and len(by_pmid["3"].sections) == 1
    assert by_pmid["2"].methods_text == "methods2" and by_pmid["2"].sections
    assert by_pmid["1"].fulltext_note == "preprint_not_archived"
    assert by_pmid["1"].methods_text is None and not by_pmid["1"].sections
    assert await _project_status(engine, project.id) == ProjectStatus.FROZEN


async def test_contract_fake_volume_zero_empty_success(engine):
    """volume=0 -> empty success: iter_ids is never called, no batch."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeSourceAdapter(volume=0, ids=[])
    result = await _import_pubmed(str(project.id), "q", session_factory=factory, adapter=fake)
    assert [c[0] for c in fake.calls] == ["search_volume"]  # pas d'iter_ids
    assert result["imported"] == 0
    assert result["total_count"] == 0
    assert result["truncated"] is False
    assert result["failed"] == 0 and result["fulltext"] == 0


async def test_contract_fake_truncated_by_limit():
    """iter_ids respects the limit cap: the first N, order preserved, the rest not generated."""
    fake = FakeSourceAdapter(
        volume=50, ids=[f"P{i}" for i in range(50)], meta_by_id={f"P{i}": {} for i in range(50)}
    )
    got = [nid async for nid in fake.iter_ids("q", None, limit=30)]
    assert len(got) == 30
    assert got == [f"P{i}" for i in range(30)]
    assert fake.calls == [("iter_ids", "q", None, 30)]


async def test_contract_fake_error_sanitized_job_failure(engine):
    """search_volume raising (outside NCBIError) -> job FAILED, project DRAFT,
    error_message without URL nor API key (C-1)."""
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeSourceAdapter(volume=3, fail_search_volume=ValueError("source indisponible (boom)"))
    with pytest.raises(ValueError):
        await _import_pubmed(str(project.id), "q", session_factory=factory, adapter=fake)
    jobs = await _jobs_for(engine, project.id)
    assert len(jobs) == 1
    assert jobs[0].status == ImportJobStatus.FAILED
    assert jobs[0].error is not None
    assert "ncbi.nlm.nih.gov" not in jobs[0].error
    assert "api_key=" not in jobs[0].error
    assert await _project_status(engine, project.id) == ProjectStatus.DRAFT


# --- B. PubMed adapter via the contract: iter_ids ≡ fetch_all_pmids -----------


def _date_range_handler(records: dict[str, int]):
    """esearch handler: count (retmax=0) or idlist bounded by the [dp] term.

    ``records``: pmid -> day ordinal; a term without [dp] bounds covers everything.
    """
    import re

    def in_range(term: str) -> list[str]:
        dates = re.findall(r'"(\d{4}/\d\d/\d\d)"', term)
        if not dates:
            return list(records)
        lo, hi = (date.fromisoformat(x.replace("/", "-")).toordinal() for x in dates)
        return [p for p, day in records.items() if lo <= day <= hi]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("esearch.fcgi")
        term = request.url.params["term"]
        retmax = int(request.url.params.get("retmax", "0"))
        ids = in_range(term)
        if retmax == 0:
            return httpx.Response(200, json={"esearchresult": {"count": str(len(ids))}})
        return httpx.Response(200, json={"esearchresult": {"idlist": ids[:retmax]}})

    return handler


async def test_iter_ids_under_window_equals_fetch_all_pmids():
    """Under the NCBI window: same list, same order, 2 esearch (count + ids)."""
    ids = [str(1000 + i) for i in range(50)]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "50"}})
            assert request.url.params["retmax"] == "50"  # min(count, plafond d'import)
            return httpx.Response(200, json={"esearchresult": {"idlist": ids}})
        raise AssertionError(f"chemin inattendu : {request.url.path}")

    async with _client(handler) as http:
        adapter = PubMedAdapter(http, "key")
        await adapter.search_volume("q", None)
        got = [nid async for nid in adapter.iter_ids("q", None)]
    assert calls["n"] == 2  # count + ids : pas de 3ᵉ esearch
    async with _client(handler) as http:
        ref = await fetch_all_pmids("q", "key", http, 50)
    assert got == ref == ids


async def test_iter_ids_bisection_order_and_calls(monkeypatch):
    """Beyond the window: same call sequence and same order as
    fetch_all_pmids (bisection, newest first)."""
    from app import pubmed

    monkeypatch.setattr(pubmed, "PUBMED_WINDOW", 5)
    # 40 articles spread over 40 consecutive days of 2021 (pmid = 1000 + day)
    records = {str(1000 + d): date(2021, 1, 1).toordinal() + d for d in range(40)}
    handler = _date_range_handler(records)
    esearches: list[tuple[str, int]] = []

    def spy(request: httpx.Request) -> httpx.Response:
        term = request.url.params["term"]
        retmax = int(request.url.params.get("retmax", "0"))
        esearches.append((term, retmax))
        return handler(request)

    async with _client(spy) as http:
        adapter = PubMedAdapter(http, "key")
        await adapter.search_volume("sleep", None)
        got = [nid async for nid in adapter.iter_ids("sleep", None)]
    adapter_calls = list(esearches)
    esearches.clear()
    async with _client(spy) as http:
        ref = await fetch_all_pmids("sleep", "key", http, 40)
    assert got == ref
    assert got == sorted(got, reverse=True)  # strictly decreasing, most recent first
    assert got[0] == "1039"
    # Same esearch calls (bounded terms + retmax): the adapter makes 1 extra count.
    assert adapter_calls[1:] == esearches


async def test_iter_ids_user_bounds():
    """User bounds: the bounded [dp] term appears only in the term
    (no datetype/mindate/maxdate)."""
    ids = [str(1000 + i) for i in range(50)]
    terms: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("esearch.fcgi")
        terms.append(request.url.params["term"])
        for absent in ("datetype", "mindate", "maxdate"):
            assert absent not in request.url.params
        if request.url.params.get("retmax") == "0":
            return httpx.Response(200, json={"esearchresult": {"count": "50"}})
        return httpx.Response(200, json={"esearchresult": {"idlist": ids}})

    async with _client(handler) as http:
        adapter = PubMedAdapter(http, "key")
        got = [
            nid
            async for nid in adapter.iter_ids(
                "q", DateBounds(date_from=date(2024, 1, 1), date_to=date(2025, 1, 1))
            )
        ]
    assert got == ids
    expected = '(q) AND ("2024/01/01"[dp] : "2025/01/01"[dp])'
    assert terms == [expected, expected]  # the term is bounded (count + ids)


async def test_iter_ids_limit_cap_most_recent():
    """limit cap beyond the window: the first N (the newest),
    NCBI calls identical to fetch_all_pmids(limit=N)."""
    # 20000 articles over 20000 consecutive days since 1970-01-01 (pmid = 1000 + day)
    start = date(1970, 1, 1)
    records = {str(1000 + d): start.toordinal() + d for d in range(20000)}
    handler = _date_range_handler(records)
    esearches: list[tuple[str, int]] = []

    def spy(request: httpx.Request) -> httpx.Response:
        term = request.url.params["term"]
        retmax = int(request.url.params.get("retmax", "0"))
        esearches.append((term, retmax))
        return handler(request)

    async with _client(spy) as http:
        adapter = PubMedAdapter(http, "key")
        await adapter.search_volume("q", None)
        got = [nid async for nid in adapter.iter_ids("q", None, limit=1500)]
    adapter_calls = list(esearches)
    esearches.clear()
    async with _client(spy) as http:
        ref = await fetch_all_pmids("q", "key", http, 20000, 1500)
    assert len(got) == 1500
    assert got == ref  # identical NCBI calls → same result
    assert got == sorted(got, reverse=True)
    assert got[0] == str(1000 + 19999)  # the most recent
    assert adapter_calls[1:] == esearches


# --- C. Cross-cutting rules (observable through the contract) ----------------


async def test_adapter_503_retries_and_error_sanitized(monkeypatch):
    """Transient 503 → retry (growing backoff); persistent 503 → NCBIError without URL."""
    sleeps: list[float] = []

    async def spy(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.pubmed._sleep", spy)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"esearchresult": {"count": "5"}})

    async with _client(handler) as http:
        adapter = PubMedAdapter(http, None)
        assert await adapter.search_volume("q", None) == 5
    assert calls["n"] == 3  # 1 + 2 retries
    # 1 throttle (1/3 s) + 2 backoff retry croissants (_RETRIES = 2)
    assert sleeps == [1 / 3, 1.0, 2.0]
    # Definitive failure: sanitized NCBIError (C-1, no URL nor key).
    fails = {"n": 0}

    def failing(request: httpx.Request) -> httpx.Response:
        fails["n"] += 1
        return httpx.Response(503, text="Service Unavailable")

    async with _client(failing) as http:
        with pytest.raises(NCBIError) as exc_info:
            await PubMedAdapter(http, None).search_volume("q", None)
    assert fails["n"] == 3  # 1 + 2 retries, puis abandon
    assert str(exc_info.value) == "HTTP 503"
    assert "ncbi.nlm.nih.gov" not in str(exc_info.value)
    assert "eutils" not in str(exc_info.value)


async def test_adapter_timeout_retries_and_sanitized(monkeypatch):
    """Transient timeout → retry; persistent timeout → NCBIError('timeout') without URL."""
    sleeps: list[float] = []

    async def spy(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.pubmed._sleep", spy)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"esearchresult": {"count": "1"}})

    async with _client(handler) as http:
        adapter = PubMedAdapter(http, None)
        assert await adapter.search_volume("q", None) == 1
    assert calls["n"] == 3
    # 1 throttle (1/3 s) + 2 backoff retry croissants
    assert sleeps == [1 / 3, 1.0, 2.0]
    fails = {"n": 0}

    def failing(request: httpx.Request) -> httpx.Response:
        fails["n"] += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async with _client(failing) as http:
        with pytest.raises(NCBIError) as exc_info:
            await PubMedAdapter(http, None).search_volume("q", None)
    assert fails["n"] == 3  # timeout exhausted (1 + 2 retries)
    assert str(exc_info.value) == "timeout"
    assert "ncbi.nlm.nih.gov" not in str(exc_info.value)


async def test_adapter_metadata_oversize_payload_rejected():
    """XML > 10 MB -> refused (I5), message without URL nor key (C-1)."""
    big = "<PubmedArticleSet><x>" + "A" * (10 * 1024 * 1024 + 1) + "</x></PubmedArticleSet>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("efetch.fcgi")
        return httpx.Response(200, text=big)

    async with _client(handler) as http:
        with pytest.raises(ValueError, match="too large") as exc_info:
            await PubMedAdapter(http, "key").fetch_metadata(["1"])
    assert "ncbi.nlm.nih.gov" not in str(exc_info.value)
    assert "key" not in str(exc_info.value)


async def test_adapter_throttle_intervals(monkeypatch):
    """Per-process throttle: one sleep per NCBI request (esearch/efetch, including
    full text), at the no-key interval (1/3 s)."""
    from app import pubmed

    sleeps: list[float] = []

    async def spy(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.pubmed._sleep", spy)
    meta_xml = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1000</PMID>
<Article><ArticleTitle>T</ArticleTitle></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC1</ArticleId></ArticleIdList>
</PubmedData></PubmedArticle></PubmedArticleSet>"""
    pmc_xml = """<?xml version="1.0"?>
<pmc-articleset><article><body><sec><title>Methods</title>
<p>We used a randomised design.</p></sec></body></article></pmc-articleset>"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "50"}})
            return httpx.Response(
                200, json={"esearchresult": {"idlist": [str(1000 + i) for i in range(50)]}}
            )
        if request.url.path.endswith("efetch.fcgi"):
            if request.url.params.get("db") == "pmc":
                return httpx.Response(200, text=pmc_xml)
            return httpx.Response(200, text=meta_xml)
        raise AssertionError(f"chemin inattendu : {request.url.path}")

    async with _client(handler) as http:
        adapter = PubMedAdapter(http, None)
        await adapter.search_volume("q", None)
        async for _ in adapter.iter_ids("q", None):
            pass
        await adapter.fetch_metadata(["1000"])
        result = await adapter.fetch_fulltext("PMC1")
    # 2 esearch (count + ids, short-circuit < window) + 1 efetch metadata
    # + 1 efetch full text: the exact count of a fetch_all_pmids equivalent.
    assert calls["n"] == 4
    # Throttle before each request, no-key interval (full text,
    # like _fetch_pmc_xml, throttled with the None key).
    assert sleeps == [pubmed._INTERVAL_NO_KEY] * 4
    assert result is not None and result[0] == "We used a randomised design."


# --- D. Contract conformance (fake + adapter) -------------------------------


def test_pubmed_adapter_is_source_adapter():
    """PubMedAdapter exposes the 4 Protocol methods, compatible signatures."""
    adapter = PubMedAdapter(_client(lambda r: httpx.Response(200)), None)
    for name in ("search_volume", "iter_ids", "fetch_metadata", "fetch_fulltext"):
        got = inspect.signature(getattr(adapter, name))
        want = inspect.signature(getattr(SourceAdapter, name))
        want_params = [p for p in want.parameters if p != "self"]
        assert list(got.parameters) == want_params, name
        assert got.return_annotation == want.return_annotation, name


async def test_fake_is_source_adapter():
    """The fake satisfies the Protocol; minimal contract semantics."""
    fake = FakeSourceAdapter(
        volume=1,
        ids=["1"],
        meta_by_id={"1": {"native_id": "1", "pmid": "1", "title": "t", "pmcid": "PMC1"}},
        fulltext_by_id={"PMC1": ("m", [])},
    )
    for name in ("search_volume", "iter_ids", "fetch_metadata", "fetch_fulltext"):
        got = inspect.signature(getattr(fake, name))
        want = inspect.signature(getattr(SourceAdapter, name))
        want_params = [p for p in want.parameters if p != "self"]
        assert list(got.parameters) == want_params, name
        assert got.return_annotation == want.return_annotation, name
    assert [nid async for nid in fake.iter_ids("q", None)] == ["1"]
    assert await fake.fetch_metadata([]) == []
    assert await fake.fetch_fulltext("nope") is None


def test_datebounds_defaults():
    """DateBounds: defaults None, frozen (immutability)."""
    assert DateBounds() == DateBounds(None, None)
    assert DateBounds().date_from is None
    assert DateBounds().date_to is None
    b = DateBounds(date(2024, 1, 1), date(2025, 1, 1))
    assert b.date_from == date(2024, 1, 1) and b.date_to == date(2025, 1, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.date_from = date(2025, 1, 1)
