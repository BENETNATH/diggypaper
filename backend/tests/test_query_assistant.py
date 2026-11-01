"""PubMed query assistant: prompt, verification by PubMed (volume, terms, recall), route."""

import httpx
import pytest
from test_projects_api import _create_project  # noqa: F401  (aide)

from app import query_assistant as qa
from app.db.models import ModelRegistry


def test_prompt_and_query_cleaning():
    p = qa.build_prompt(
        [
            {"role": "user", "content": "sleep and memory"},
            {"role": "assistant", "content": "Which?"},
        ]
    )
    assert "<conversation>" in p and "User: sleep and memory" in p and "Assistant: Which?" in p
    assert (
        qa.clean_query('```pubmed\n("sleep"[tiab])\n  AND memory\n```')
        == '("sleep"[tiab]) AND memory'
    )
    assert qa.clean_query(None) is None and qa.clean_query("  ") is None


def _ncbi(query_hits):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "eutils.ncbi.nlm.nih.gov":
            term = request.url.params["term"]
            if "[uid]" in term:  # recall test: only 111 and 222 are found
                return httpx.Response(200, json={"esearchresult": {"idlist": query_hits}})
            return httpx.Response(
                200,
                json={
                    "esearchresult": {
                        "count": "1234",
                        "querytranslation": '"sleep"[MeSH Terms] OR sleep[tiab]',
                        "errorlist": {"phrasesnotfound": ["Fake Heading[MeSH Terms]"]},
                    }
                },
            )
        if host == "www.ebi.ac.uk":
            return httpx.Response(
                200, json={"resultList": {"result": [{"pmid": "333", "doi": "10.1000/found"}]}}
            )
        raise AssertionError(request.url)

    return handler


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


async def test_check_query_reports_volume_unknown_terms_and_recall():
    seeds = qa.parse_seeds("111\n222\n10.1000/found\n10.1000/unknown\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ncbi(["111", "222", "999"]))) as h:
        out = await qa.check_query("sleep AND memory", None, seeds, h)
    assert out["count"] == 1234 and out["translation"].startswith('"sleep"[MeSH Terms]')
    assert out["not_found"] == ["Fake Heading[MeSH Terms]"] and out["too_long"] is False
    # 3 known PMIDs (111, 222, 333 via DOI) + 1 unfindable DOI; the query finds 2 of them
    assert out["recall"]["total"] == 4 and out["recall"]["found"] == 2
    assert out["recall"]["missing"] == ["333", "10.1000/unknown"]


async def test_assistant_route(client, session, researcher_user, do_login, monkeypatch):
    from app.api.v1 import query_assistant as route

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = (await client.post("/api/v1/projects", json={"title": "P"})).json()["id"]
    session.add(ModelRegistry(name="Mdl", model_identifier="x", owner_id=researcher_user.id))
    await session.commit()
    seen = {}

    async def fake_model(model, service, system, prompt):
        seen["prompt"] = prompt
        return {"reply": "Voici.", "query": "```\n(sleep[tiab]) AND memory[tiab]\n```"}

    async def fake_check(query, api_key, seeds, http):
        return {"count": 42, "translation": query, "not_found": [], "too_long": False}

    monkeypatch.setattr(route, "infer_with", fake_model)
    monkeypatch.setattr(route.qa, "check_query", fake_check)
    url = f"/api/v1/projects/{pid}/query-assistant"
    body = {"model": "Mdl", "messages": [{"role": "user", "content": "sommeil et mémoire"}]}
    r = await client.post(url, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["query"] == "(sleep[tiab]) AND memory[tiab]" and d["check"]["count"] == 42
    assert "sommeil et mémoire" in seen["prompt"]
    # last message of the assistant, unknown model: refused
    bad = {**body, "messages": [{"role": "assistant", "content": "x"}]}
    assert (await client.post(url, json=bad)).status_code == 422
    assert (await client.post(url, json={**body, "model": "Nope"})).status_code == 422

    async def vague(model, service, system, prompt):
        return {"reply": "Quelle population ?", "query": None}

    monkeypatch.setattr(route, "infer_with", vague)
    r = await client.post(url, json=body)
    assert r.json() == {
        "reply": "Quelle population ?",
        "query": None,
        "refused": False,
        "check": None,
    }

    # Misuse: off-topic free text never comes out
    essay = "Here is a long essay about something else. " * 20
    for out in (
        {"reply": essay, "query": None},  # off-topic reply
        {"reply": "REFUSED", "query": None},  # model refusal
        {"reply": "ok", "query": essay},  # prose slipped into "query"
        {"reply": "x?", "query": "tell me a joke about cats"},  # idem, sans syntaxe PubMed
    ):

        async def abused(model, service, system, prompt, _out=out):
            return _out

        monkeypatch.setattr(route, "infer_with", abused)
        d = (await client.post(url, json=body)).json()
        assert (
            d["refused"] is True and d["reply"] == "" and d["query"] is None and d["check"] is None
        )
    # input limits: message too long, conversation too long
    long = {"model": "Mdl", "messages": [{"role": "user", "content": "x" * 1001}]}
    assert (await client.post(url, json=long)).status_code == 422
    many = {"model": "Mdl", "messages": [{"role": "user", "content": "a"}] * 9}
    assert (await client.post(url, json=many)).status_code == 422


def test_constrain_keeps_valid_query_with_a_bounded_explanation():
    out = qa.constrain({"reply": "x" * 900, "query": '"sleep"[MeSH Terms] AND memory[tiab]'})
    assert out["refused"] is False and len(out["reply"]) == qa.MAX_REPLY and out["query"]
    assert qa.looks_like_query("(a OR b) AND c") and not qa.looks_like_query("write me a poem")


def test_constrain_openalex_accepts_plain_text_and_rejects_paragraphs():
    out = qa.constrain({"reply": "ok", "query": "sleep memory healthy adults"}, source="openalex")
    assert out["refused"] is False and out["query"] == "sleep memory healthy adults"
    out = qa.constrain({"reply": "ok", "query": "line one\nline two"}, source="openalex")
    assert out["query"] is None
    rules = qa.system_prompt("openalex").split("Rules for the query:")[1].split("- Do not add")[0]
    assert "OpenAlex" in rules and "NO Boolean operators" in rules


async def test_assistant_route_non_pubmed_source_checks_volume_only(
    client, session, researcher_user, do_login, monkeypatch
):
    from app.api.v1 import query_assistant as route

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = (await client.post("/api/v1/projects", json={"title": "P"})).json()["id"]
    session.add(ModelRegistry(name="Mdl", model_identifier="x", owner_id=researcher_user.id))
    await session.commit()
    seen = {}

    async def fake_model(model, service, system, prompt):
        seen["system"] = system
        return {"reply": "ok", "query": "(sleep) AND memory"}

    async def fake_volume(self, query, bornes):
        return 7

    monkeypatch.setattr(route, "infer_with", fake_model)
    monkeypatch.setattr(route.ADAPTERS["europepmc"], "search_volume", fake_volume)
    url = f"/api/v1/projects/{pid}/query-assistant"
    body = {
        "model": "Mdl",
        "source": "europepmc",
        "messages": [{"role": "user", "content": "sommeil et mémoire"}],
    }
    r = await client.post(url, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["query"] == "(sleep) AND memory" and d["check"] == {"count": 7, "too_long": False}
    assert "Europe PMC" in seen["system"]
    # unknown source -> 422 (no crash)
    assert (await client.post(url, json={**body, "source": "crossref"})).status_code == 422
