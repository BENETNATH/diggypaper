"""Phase 2 multi-sources : ``source`` dans l'API (spec 2026-10-29-sources-phase2-identite D6)
— 100 % offline."""

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
    """POST /import sans ``source`` → la tâche reçoit source='pubmed'."""
    pid = await _project(client, do_login)
    delay = _mock_delay(monkeypatch)
    resp = await client.post(f"/api/v1/projects/{pid}/import", json={"query": "cancer"})
    assert resp.status_code == 202
    assert delay.call_args.kwargs["source"] == "pubmed"


async def test_import_unknown_source_422(client, researcher_user, do_login, monkeypatch):
    """Source absente du registre d'adapters → 422, aucune tâche lancée."""
    pid = await _project(client, do_login)
    delay = _mock_delay(monkeypatch)
    resp = await client.post(
        f"/api/v1/projects/{pid}/import", json={"query": "cancer", "source": "hal"}
    )
    assert resp.status_code == 422
    delay.assert_not_called()


async def test_query_volume_unknown_source_422(client, researcher_user, do_login, monkeypatch):
    """query-volume partage la validation de ``source`` (même modèle) : 422 sans appel réseau."""
    import app.api.v1.projects as projects_api

    pid = await _project(client, do_login)

    def no_network():
        raise AssertionError("aucun appel réseau attendu")

    monkeypatch.setattr(projects_api, "_make_http_client", no_network)
    resp = await client.post(
        f"/api/v1/projects/{pid}/query-volume", json={"query": "cancer", "source": "hal"}
    )
    assert resp.status_code == 422


async def test_jobs_list_exposes_source(client, session, researcher_user, do_login):
    """GET /jobs : la source de chaque import (null pour un import de références)."""
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
    """Liste des articles et détail : ``source`` + ``native_id`` (null pour l'ancien)."""
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


# --- query-volume par source (spec 2026-10-29-sources-phase3 D4) --------------


@pytest.fixture(autouse=True)
def _no_epmc_sleep(monkeypatch):
    """Pas de vrai sleep en tests (throttle EPMC sur query-volume)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.europepmc._sleep", _noop)


async def test_query_volume_europepmc(client, researcher_user, do_login, monkeypatch):
    """source='europepmc' → /search EPMC (aucun appel NCBI), bornes dates portées."""
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
    """503 EPMC persistant (retries inclus) → 502 {error: europepmc_unavailable}, sans URL."""
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
