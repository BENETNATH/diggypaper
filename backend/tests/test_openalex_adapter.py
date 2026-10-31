"""Phase 4a : adapter OpenAlex (spec 2026-10-29-sources-phase4a D1) — 100 % offline."""

import httpx
import pytest

from app.openalex import _INTERVAL, OpenAlexAdapter, OpenAlexError
from app.sources import DateBounds

W1 = "https://openalex.org/W190488077"
W2 = "https://openalex.org/W2056724834"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _works_resp(
    results: list[dict], count: int, page: int = 1, per_page: int = 25
) -> httpx.Response:
    return httpx.Response(
        200, json={"meta": {"count": count, "page": page, "per_page": per_page}, "results": results}
    )


WORK = {
    "id": W1,
    "display_name": "A p53 review",
    "doi": "https://doi.org/10.1016/s0092-8674(00)00171-9",
    "publication_date": "2000-11-01",
    "language": "en",
    "abstract_inverted_index": {"p53": [0], "review": [1], "article": [2]},
    "authorships": [
        {"author": {"display_name": "Karen H. Vousden"}},
        {"author": None},  # author manquant : sauté
    ],
    "primary_location": {"source": {"display_name": "Cell"}},
    "ids": {"openalex": W1, "pmid": "https://pubmed.ncbi.nlm.nih.gov/10767502"},
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.openalex._sleep", _noop)


# --- search_volume ---------------------------------------------------------------


async def test_search_volume_params_and_count():
    """``/works?search=…&per_page=1`` -> ``meta.count`` (no separate count call)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openalex.org"
        assert request.url.path == "/works"
        seen.update(dict(request.url.params))
        return _works_resp([], 42)

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        assert await ad.search_volume("p53", DateBounds()) == 42
    assert seen["search"] == "p53"
    assert seen["per_page"] == "1"
    assert "filter" not in seen
    assert "api_key" not in seen


async def test_search_volume_date_bounds_in_filter():
    """Date bounds -> ``filter`` (from/to_publication_date), never a request parameter."""
    from datetime import date

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("filter"))
        return _works_resp([], 7)

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        await ad.search_volume(
            "p53", DateBounds(date.fromisoformat("2024-01-15"), date.fromisoformat("2024-06-30"))
        )
        await ad.search_volume("p53", DateBounds(date_to=date.fromisoformat("2024-06-30")))
    assert seen[0] == "from_publication_date:2024-01-15,to_publication_date:2024-06-30"
    assert seen[1] == "to_publication_date:2024-06-30"


async def test_search_volume_api_key_sent_when_set():
    """Key set -> ``api_key`` parameter; without key -> no param (free IP pool)."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return _works_resp([], 1)

    async with _client(handler) as http:
        assert await OpenAlexAdapter(http, "KEY123").search_volume("q", DateBounds()) == 1
        assert await OpenAlexAdapter(http, None).search_volume("q", DateBounds()) == 1
    assert seen[0]["api_key"] == "KEY123"
    assert "api_key" not in seen[1]


# --- iter_ids ----------------------------------------------------------------------


async def test_iter_ids_page_pagination():
    """page 1 -> 2 (per_page ≤ 200, sort publication_date:desc, bounds in filter);
    W… ids without prefix; deduplicated; stops when page x per_page covers the count."""
    calls: list[dict] = []
    from datetime import date

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        assert p["sort"] == "publication_date:desc"
        if p["page"] == "1":
            return _works_resp(
                [{"id": W2}, {"id": W1}, {"id": W2}], count=300, page=1, per_page=int(p["per_page"])
            )
        assert p["page"] == "2"
        return _works_resp([], count=300, page=2, per_page=int(p["per_page"]))

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        ids = [
            i async for i in ad.iter_ids("p53", DateBounds(date_from=date(2024, 1, 1)), limit=10)
        ]
    assert ids == ["W2056724834", "W190488077"]  # première occurrence, sans URL
    assert calls[0]["search"] == "p53" and calls[0]["per_page"] == "10"  # min(200, limit)
    assert calls[0]["filter"] == "from_publication_date:2024-01-01"
    assert len(calls) == 2


async def test_iter_ids_limit_stops_early():
    """limit < page: stop after the 1st page, ids in first-occurrence order."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _works_resp([{"id": W1}, {"id": W2}], count=100, page=1, per_page=200)

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        ids = [i async for i in ad.iter_ids("q", DateBounds(), limit=1)]
    assert ids == ["W190488077"]


# --- fetch_metadata -----------------------------------------------------------------


async def test_fetch_metadata_one_get_per_id_and_parse():
    """One ``GET /works/{id}`` per id; inverted abstract reconstructed; doi without prefix;
    journal / authors / pub_date / language; bare pmid from ``ids``; fulltext_note set."""
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/works/")
        fetched.append(request.url.path.removeprefix("/works/"))
        work = dict(WORK, id=f"https://openalex.org/{request.url.path.removeprefix('/works/')}")
        if work["id"] == W2:
            work = {
                "id": W2,
                "display_name": "Nature paper",
                "doi": "https://doi.org/10.1038/nature12374",
                "publication_date": "2012-11-01",
                "language": "en",
                "primary_location": {"source": {"display_name": "Nature"}},
                "ids": {
                    "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3716088/",
                },
            }
        return httpx.Response(200, json=work)

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        metas = await ad.fetch_metadata(["W190488077", "W2056724834"])
    assert fetched == ["W190488077", "W2056724834"]  # un appel par id, dans l'ordre
    assert await OpenAlexAdapter(http, None).fetch_metadata([]) == []
    w1, w2 = metas
    assert w1 == {
        "native_id": "W190488077",
        "title": "A p53 review",
        "abstract": "p53 review article",
        "language": "en",
        "journal": "Cell",
        "pub_date": "2000-11-01",
        "authors": ["Karen H. Vousden"],
        "doi": "10.1016/s0092-8674(00)00171-9",
        "pmid": "10767502",
        "fulltext_note": "metadata_only",
    }
    assert w2["native_id"] == "W2056724834"
    assert w2["doi"] == "10.1038/nature12374"
    assert w2["pmcid"] == "PMC3716088"
    assert w2["journal"] == "Nature"
    assert w2["abstract"] is None  # absente dans le work → None (clé principale)
    assert "pmid" not in w2 and "authors" not in w2  # clés secondaires absentes
    assert w2["fulltext_note"] == "metadata_only"


# --- fetch_fulltext / erreurs / throttle ---------------------------------------------


async def test_fetch_fulltext_always_none():
    """Metadata-only source: never full text (-> fulltext_note pipeline)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("aucun appel HTTP attendu")

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        assert await ad.fetch_fulltext("W123") is None
        assert await ad.fetch_fulltext("") is None


async def test_retry_on_429_and_error_sanitized(monkeypatch):
    """429 -> backoff + retry; ``OpenAlexError`` without URL in the message; _INTERVAL throttle."""
    sleeps: list[float] = []

    async def _rec(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("app.openalex._sleep", _rec)
    states = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        states["n"] += 1
        if states["n"] == 1:
            return httpx.Response(429, text="rate limit")
        return _works_resp([], 3)

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        assert await ad.search_volume("q", DateBounds()) == 3
    assert states["n"] == 2  # 429 → retry → succès
    assert sleeps[0] == _INTERVAL  # throttle avant le 1er appel
    assert len(sleeps) == 2 and all(s >= _INTERVAL for s in sleeps)


async def test_error_message_has_no_url():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as http:
        ad = OpenAlexAdapter(http, None)
        with pytest.raises(OpenAlexError) as exc:
            await ad.search_volume("q", DateBounds())
    assert "api.openalex.org" not in str(exc.value)
    assert "HTTP 500" in str(exc.value)
