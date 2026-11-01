"""Phase 2 multi-sources: DOI dedup at import (spec
2026-10-29-sources-phase2-identite D5) — 100% offline."""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import prisma
from app.db.models import Article, Project, ProjectStatus
from app.refimport import clean_doi
from app.tasks import _import_pubmed


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_project(engine, *articles: dict) -> Project:
    async with _factory(engine)() as session:
        project = Project(title="Dedup")
        session.add(project)
        await session.flush()
        session.add_all(Article(project_id=project.id, **a) for a in articles)
        await session.commit()
        return project


async def _articles(engine, project_id) -> list[Article]:
    async with _factory(engine)() as session:
        return list(
            (await session.execute(select(Article).where(Article.project_id == project_id)))
            .scalars()
            .all()
        )


def _efetch_xml(items: list[tuple[str, str | None, str | None]]) -> str:
    """PubMed efetch: one PubmedArticle per (pmid, doi, pmcid)."""
    out = []
    for pmid, doi, pmcid in items:
        ids = f'<ArticleId IdType="pubmed">{pmid}</ArticleId>'
        ids += f'<ArticleId IdType="doi">{doi}</ArticleId>' if doi else ""
        ids += f'<ArticleId IdType="pmc">{pmcid}</ArticleId>' if pmcid else ""
        out.append(
            f"""<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>
<ArticleTitle>Title {pmid}</ArticleTitle><Language>English</Language></Article>
</MedlineCitation><PubmedData><ArticleIdList>{ids}</ArticleIdList></PubmedData>
</PubmedArticle>"""
        )
    return f"<PubmedArticleSet>{''.join(out)}</PubmedArticleSet>"


def _ncbi(items: list[tuple[str, str | None, str | None]]):
    """PubMed esearch + efetch; logs full-text calls (efetch db=pmc)."""
    pmc_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("esearch.fcgi"):
            if params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": str(len(items))}})
            return httpx.Response(200, json={"esearchresult": {"idlist": [i[0] for i in items]}})
        if path.endswith("efetch.fcgi"):
            if params.get("db") == "pmc":
                pmc_calls.append(params.get("id", ""))
                return httpx.Response(200, text="<pmc-articleset/>")
            return httpx.Response(200, text=_efetch_xml(items))
        raise AssertionError(f"appel inattendu : {request.url}")

    return pmc_calls, httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _import(engine, project, items):
    pmc_calls, http = _ncbi(items)
    async with http:
        result = await _import_pubmed(
            str(project.id), "q", session_factory=_factory(engine), http=http
        )
    return result, pmc_calls


async def _redraft(engine, project_id) -> None:
    async with _factory(engine)() as session:
        (await session.get(Project, project_id)).status = ProjectStatus.DRAFT
        await session.commit()


async def test_doi_duplicate_other_identity_linked_fills_and_fulltext(engine):
    """A project article (different identity: imported reference) already carries this DOI:
    the PubMed record is ATTACHED (spec phase 3 D3, no more skip) — empty fields filled,
    full text requested for the row, existing identity and origin intact."""

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("esearch.fcgi"):
            if params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": "2"}})
            return httpx.Response(200, json={"esearchresult": {"idlist": ["111", "222"]}})
        if path.endswith("efetch.fcgi"):
            if params.get("db") == "pmc":
                assert params.get("id") == "PMC111"
                # JATS long enough to pass ``long_enough``.
                return httpx.Response(
                    200,
                    text=(
                        "<article><body>"
                        "<sec><title>Methods</title><p>" + "M" * 1500 + "</p></sec>"
                        "<sec><title>Results</title><p>" + "R" * 100 + "</p></sec>"
                        "</body></article>"
                    ),
                )
            return httpx.Response(
                200,
                text=_efetch_xml(
                    [("111", "https://doi.org/10.1000/abc", "PMC111"), ("222", None, None)]
                ),
            )
        raise AssertionError(f"appel inattendu : {request.url}")

    project = await _make_project(
        engine, {"doi": "10.1000/ABC", "title": "Existing", "origin": "references"}
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        result = await _import_pubmed(
            str(project.id), "q", session_factory=_factory(engine), http=http
        )
    assert result["imported"] == 1 and result["failed"] == 0
    assert result["duplicates_doi"] == 1
    arts = await _articles(engine, project.id)
    assert sorted(a.pmid for a in arts) == ["111", "222"]
    existing = next(a for a in arts if a.pmid == "111")
    assert (existing.title, existing.origin, existing.doi) == (
        "Existing",
        "references",
        "10.1000/ABC",
    )
    assert existing.source is None and existing.native_id is None  # identity intact
    assert existing.pmcid == "PMC111"  # empty field completed
    assert existing.sections and existing.fulltext_source == "pmc"
    assert existing.fulltext_url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC111/"


async def test_doi_duplicate_within_same_import(engine):
    """Two PMIDs from the same import share a DOI (different case/prefix): the first comes in,
    the second is counted ``duplicates_doi``."""
    project = await _make_project(engine)
    result, _ = await _import(
        engine, project, [("111", "10.1000/X", None), ("222", "doi:10.1000/x", None)]
    )
    assert (result["imported"], result.get("duplicates_doi")) == (1, 1)
    assert [a.pmid for a in await _articles(engine, project.id)] == ["111"]


async def test_reimport_same_identity_with_doi_is_not_doi_duplicate(engine):
    """Guard: re-importing the same records (same identity) is NOT a DOI duplicate —
    0 imported, no ``duplicates_doi``, as before."""
    project = await _make_project(engine)
    items = [("111", "10.1000/a", None), ("222", "10.1000/b", None)]
    await _import(engine, project, items)
    await _redraft(engine, project.id)
    again, _ = await _import(engine, project, items)
    assert again["imported"] == 0 and again["failed"] == 0
    assert again.get("duplicates_doi", 0) == 0
    assert len(await _articles(engine, project.id)) == 2


async def test_import_without_doi_duplicate_keeps_stats_payload(engine):
    """Guard: without a DOI duplicate, the stats payload is exactly the old one (the
    ``duplicates_doi`` key only appears if > 0)."""
    project = await _make_project(engine)
    result, _ = await _import(engine, project, [("111", "10.1000/a", None)])
    assert set(result) == {"imported", "failed", "fulltext", "total_count", "truncated"}


def test_clean_doi_normalizes_prefixes_and_case():
    """Guard: the dedup key is ``refimport.clean_doi`` (reused, not rewritten)."""
    variants = [
        "10.1000/ABC.1",
        "https://doi.org/10.1000/abc.1",
        "http://dx.doi.org/10.1000/ABC.1",
        "doi:10.1000/abc.1.",
        " DOI: 10.1000/Abc.1 ",
    ]
    assert {clean_doi(v) for v in variants} == {"10.1000/abc.1"}


def test_prisma_counts_doi_duplicates():
    """PRISMA: DOI duplicates skipped at import count in "identified" and in
    "duplicates removed"."""
    job = SimpleNamespace(
        status=SimpleNamespace(value="COMPLETED"),
        result_json={"imported": 2, "failed": 0, "duplicates_doi": 1},
    )
    articles = [SimpleNamespace(id=i, sections=None, origin="pubmed") for i in (1, 2)]
    counts = prisma.prisma_counts(articles, {}, {}, [job])
    assert counts["identified"] == 3
    assert counts["duplicates"] == 1
