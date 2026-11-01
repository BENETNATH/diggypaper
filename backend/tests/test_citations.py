"""Citation chaining: links (mocked iCite), ranking, endpoint, import and created search."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tasks_import import _efetch_xml, _make_project

from app import citations
from app.db.models import Article, Search, SearchArticle
from app.tasks import _import_pubmed

ICITE = {
    "1": {"references": [10, 11], "cited_by": [20]},
    "2": {"references": [10], "cited_by": [20, 21]},
    "3": {"references": [12], "cited_by": [1]},  # 1 is a seed: ignored
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "icite.od.nih.gov":
        assert "legacy" not in request.url.params  # otherwise the names change (seen in the wild)
        pmids = request.url.params["pmids"].split(",")
        data = [{"pmid": int(p), **ICITE[p]} for p in pmids if p in ICITE]
        return httpx.Response(200, json={"data": data})
    if request.url.path.endswith("efetch.fcgi"):
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, text=_efetch_xml(ids, with_pmcid=False))
    raise AssertionError(f"appel inattendu : {request.url}")


async def test_links_rank_and_histogram():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        both = await citations.fetch_links(["1", "2", "3"], http)
        fwd = await citations.fetch_links(["1", "2", "3"], http, "forward")
        bwd = await citations.fetch_links(["1", "2", "3"], http, "backward")
    assert both["10"] == {"1", "2"} and both["20"] == {"1", "2"} and "1" not in both
    assert set(fwd) == {"20", "21"} and set(bwd) == {"10", "11", "12"}
    assert citations.rank(both, {"12"}, 1, 10) == [("10", 2), ("20", 2), ("11", 1), ("21", 1)]
    assert citations.rank(both, set(), 2, 10) == [("10", 2), ("20", 2)]
    assert citations.rank(both, set(), 1, 1) == [("10", 2)]  # plafond
    assert citations.overlap_histogram(both, {"10"}) == {"1": 4, "2": 1, "3": 0, "5": 0}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


async def test_import_citations_adds_candidates_and_creates_search(engine):
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Article(project_id=project.id, pmid="1", title="seed 1"),
                Article(project_id=project.id, pmid="2", title="seed 2"),
                Article(project_id=project.id, pmid="10", title="déjà là"),
            ]
        )
        await s.commit()
    opts = {
        "kind": "citations",
        "citations": {
            "seeds": ["1", "2"],
            "direction": "both",
            "min_overlap": 1,
            "max_candidates": 3,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(
            str(project.id), "citations", session_factory=factory, http=http, options=opts
        )
    # candidates: 11 (1 seed), 20 (2), 21 (1) outside the corpus; 10 is already in the corpus
    assert result["imported"] == 3 and result["candidates"] == 4 and result["search_id"]
    async with factory() as s:
        new = (await s.execute(select(Article).where(Article.origin == "citation"))).scalars().all()
        by_pmid = {a.pmid: a for a in new}
        assert set(by_pmid) == {"20", "11", "21"} and by_pmid["20"].origin_seeds == 2
        search = await s.get(Search, __import__("uuid").UUID(result["search_id"]))
        lot = (
            (
                await s.execute(
                    select(SearchArticle.article_id).where(SearchArticle.search_id == search.id)
                )
            )
            .scalars()
            .all()
        )
        assert set(lot) == {a.id for a in new}
        assert search.parent_spec["citations"]["seeds"] == 2 and not search.lot_all


async def test_citations_endpoint(client, researcher_user, do_login, session, monkeypatch):
    from unittest.mock import MagicMock

    import app.tasks as tasks
    from app.api.v1 import citations as api

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = (await client.post("/api/v1/projects", json={"title": "P"})).json()["id"]
    import uuid

    session.add_all(
        [Article(project_id=uuid.UUID(pid), pmid=p, title=p) for p in ("1", "2", "99")]
        + [Article(project_id=uuid.UUID(pid), title="sans pmid")]
    )
    from app.db.models import Project, ProjectStatus

    (await session.get(Project, uuid.UUID(pid))).status = ProjectStatus.FROZEN
    await session.commit()
    url = f"/api/v1/projects/{pid}/citations"

    async def fake_links(seeds, http, direction="both", progress=None):
        assert sorted(seeds) == ["1", "2", "99"] and direction == "both"
        return {"10": {"1", "2"}, "11": {"1"}, "99": {"1"}}

    monkeypatch.setattr(api.citations, "fetch_links", fake_links)
    dry = await client.post(url, json={"dry_run": True})
    assert dry.status_code == 202, dry.text
    d = dry.json()
    assert (d["seeds"], d["selected"], d["candidates"]) == (3, 4, 2)
    assert d["by_overlap"] == {"1": 2, "2": 1, "3": 0, "5": 0}
    delay = MagicMock(return_value=MagicMock(id="job-1", state="PENDING"))
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", delay)
    r = await client.post(
        url, json={"direction": "forward", "min_overlap": 2, "max_candidates": 50}
    )
    assert r.status_code == 202 and r.json()["job_id"] == "job-1"
    opts = delay.call_args.args[3]
    assert opts["kind"] == "citations" and opts["citations"]["min_overlap"] == 2
    assert sorted(opts["citations"]["seeds"]) == ["1", "2", "99"]
    assert (await client.post(url, json={"selection": {"q": "zzz-nothing"}})).status_code == 422
