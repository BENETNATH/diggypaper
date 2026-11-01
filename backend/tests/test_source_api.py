"""Phase 2 multi-sources: ``source`` in the API (spec 2026-10-29-sources-phase2-identite D6)
— 100% offline."""

import uuid
from unittest.mock import MagicMock

import httpx
import pytest

from app.db.models import Article, ImportJob, ImportJobStatus


async def _project(client, do_login) -> str:
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/projects", json={"title": "Sources"})
    assert resp.status_code == 201
    return resp.json()["id"]


def _mock_delay(monkeypatch) -> MagicMock:
    import app.tasks as tasks

    result = MagicMock()
    result.id, result.state = "job-1", "PENDING"
    delay = MagicMock(return_value=result)
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", delay)
    return delay


async def test_import_source_defaults_to_pubmed_and_is_forwarded(
    client, researcher_user, do_login, monkeypatch
):
    """POST /import without ``source`` -> the task receives source='pubmed'."""
    pid = await _project(client, do_login)
    delay = _mock_delay(monkeypatch)
    resp = await client.post(f"/api/v1/projects/{pid}/import", json={"query": "cancer"})
    assert resp.status_code == 202
    assert delay.call_args.kwargs["source"] == "pubmed"


async def test_import_unknown_source_422(client, researcher_user, do_login, monkeypatch):
    """Source absent from the adapter registry -> 422, no task started."""
    pid = await _project(client, do_login)
    delay = _mock_delay(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{pid}/import", json={"query": "cancer", "source": "arxiv"}
    )
    assert resp.status_code == 422
    delay.assert_not_called()


async def test_query_volume_unknown_source_422(client, researcher_user, do_login, monkeypatch):
    """query-volume shares the ``source`` validation (same model): 422 without network call."""
    import app.api.v1.projects as projects_api

    pid = await _project(client, do_login)

    def no_network():
        raise AssertionError("no network call expected")

    monkeypatch.setattr(projects_api, "_make_http_client", no_network)
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume", json={"query": "cancer", "source": "arxiv"}
    )
    assert resp.status_code == 422


async def test_jobs_list_exposes_source(client, session, researcher_user, do_login):
    """GET /jobs: the source of each import (null for a reference import)."""
    pid = await _project(client, do_login)
    session.add_all(
        [
            ImportJob(
                project_id=uuid.UUID(pid),
                query="cancer",
                source="pubmed",
                status=ImportJobStatus.COMPLETED,
            ),
            ImportJob(
                project_id=uuid.UUID(pid),
                query="references (2)",
                kind="references",
                status=ImportJobStatus.COMPLETED,
            ),
        ]
    )
    await session.commit()
    jobs = (await client.get(f"/api/v1/projects/{pid}/jobs")).json()["jobs"]
    assert {j["query"]: j["source"] for j in jobs} == {"cancer": "pubmed", "references (2)": None}


async def test_article_payloads_expose_source_and_native_id(
    client, session, researcher_user, do_login
):
    """Article list and detail: ``source`` + ``native_id`` (null for old ones)."""
    pid = await _project(client, do_login)
    art = Article(
        project_id=uuid.UUID(pid), pmid="42", source="pubmed", native_id="42", title="New"
    )
    old = Article(project_id=uuid.UUID(pid), doi="10.1/old", title="Old")
    session.add_all([art, old])
    await session.commit()
    items = (await client.get(f"/api/v1/projects/{pid}/articles")).json()["items"]
    assert {i["title"]: (i["source"], i["native_id"]) for i in items} == {
        "New": ("pubmed", "42"),
        "Old": (None, None),
    }
    detail = (await client.get(f"/api/v1/projects/{pid}/articles/{art.id}")).json()
    assert (detail["source"], detail["native_id"]) == ("pubmed", "42")


# --- query volume per source (spec 2026-10-29-sources-phase3 D4) --------------


@pytest.fixture(autouse=True)
def _no_epmc_sleep(monkeypatch):
    """No real sleep in tests (EPMC throttle on query-volume)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.europepmc._sleep", _noop)


@pytest.fixture(autouse=True)
def _no_openalex_sleep(monkeypatch):
    """No real sleep in tests (OpenAlex throttle on query-volume)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.openalex._sleep", _noop)


async def test_query_volume_europepmc(client, researcher_user, do_login, monkeypatch):
    """source='europepmc' -> EPMC /search (no NCBI call), date bounds carried."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = await _project(client, do_login)
    import app.api.v1.projects as projects_api

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.ebi.ac.uk"
        assert request.url.path == "/europepmc/webservices/rest/search"
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"hitCount": 42})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api, "_make_http_client", lambda: real(transport=httpx.MockTransport(handler))
    )
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume",
        json={
            "query": "p53",
            "source": "europepmc",
            "date_from": "2024-01-15",
            "date_to": "2024-06-30",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 42
    p = seen[0]
    assert (p["resultType"], p["pageSize"]) == ("lite", "1")
    assert p["query"] == "(p53) AND FIRST_PDATE:[2024-01-15 TO 2024-06-30]"


async def test_query_volume_europepmc_error_502(client, researcher_user, do_login, monkeypatch):
    """Persistent EPMC 503 (retries included) → 502 {error: europepmc_unavailable}, no URL."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = await _project(client, do_login)
    import app.api.v1.projects as projects_api

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="rate limited")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api, "_make_http_client", lambda: real(transport=httpx.MockTransport(handler))
    )
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume", json={"query": "p53", "source": "europepmc"}
    )
    assert resp.status_code == 502
    assert resp.json() == {"error": "europepmc_unavailable"}
    assert "ebi.ac.uk" not in resp.text


# --- query-volume OpenAlex (spec 2026-10-29-sources-phase4a D1) ----------------


async def test_query_volume_openalex(client, researcher_user, do_login, monkeypatch):
    """source='openalex' -> OpenAlex /works (no NCBI/EPMC call), count in ``meta``."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = await _project(client, do_login)
    import app.api.v1.projects as projects_api

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openalex.org"
        assert request.url.path == "/works"
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"meta": {"count": 17}, "results": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api, "_make_http_client", lambda: real(transport=httpx.MockTransport(handler))
    )
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume",
        json={"query": "p53", "source": "openalex", "date_from": "2024-01-15"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 17
    p = seen[0]
    assert p["search"] == "p53" and p["per_page"] == "1"
    assert p["filter"] == "from_publication_date:2024-01-15"


async def test_query_volume_openalex_error_502(client, researcher_user, do_login, monkeypatch):
    """Persistent OpenAlex 429 (retries included) → 502 {error: openalex_unavailable}, no URL."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = await _project(client, do_login)
    import app.api.v1.projects as projects_api

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limit")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        projects_api, "_make_http_client", lambda: real(transport=httpx.MockTransport(handler))
    )
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume", json={"query": "p53", "source": "openalex"}
    )
    assert resp.status_code == 502
    assert resp.json() == {"error": "openalex_unavailable"}
    assert "openalex.org" not in resp.text
