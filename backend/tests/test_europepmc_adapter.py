"""Phase 3 : adapter Europe PMC (spec 2026-10-29-sources-phase3 D1) — 100 % offline."""

import httpx
import pytest

from app.europepmc import _INTERVAL, EpmcError, EuropePMCAdapter
from app.sources import DateBounds, source_label, source_name

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

JATS = """<?xml version="1.0"?>
<article xml:lang="en">
  <front><article-meta>
    <title-group><article-title>Titre</article-title></title-group>
    <abstract><p>Abstract text.</p></abstract>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title><p>Intro text.</p></sec>
    <sec><title>Methods</title><p>Method text.</p>
      <sec><title>Sample</title><p>Sub text.</p></sec>
    </sec>
    <sec><title>Results</title><p>Result text.</p></sec>
  </body>
</article>"""


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _search_resp(hit: int, results: list[dict], cursor: str | None = None) -> httpx.Response:
    body = {"hitCount": hit, "resultList": {"result": results}}
    if cursor:
        body["nextCursorMark"] = cursor
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.europepmc._sleep", _noop)


# --- search_volume -------------------------------------------------------------


async def test_search_volume_params_and_count(monkeypatch):
    """``/search`` resultType=lite, pageSize=1, bounded query; hitCount → int."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https" and request.url.host == "www.ebi.ac.uk"
        assert request.url.path == "/europepmc/webservices/rest/search"
        p = request.url.params
        seen.update(dict(p))
        return _search_resp(42, [])

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        assert await ad.search_volume("q", DateBounds(date_from=None, date_to=None)) == 42
    assert seen["query"] == "q"
    assert seen["resultType"] == "lite"
    assert seen["pageSize"] == "1"
    assert "FIRST_PDATE" not in seen["query"]
    assert "sort" not in seen  # the count does not sort (EBI 503 of 2026-10-31 on any sorted query)


async def test_search_volume_date_bounds(monkeypatch):
    """Bounds → FIRST_PDATE:[lo TO hi] ANDed; open bounds → raw query."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return _search_resp(1, [])

    from datetime import date

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        await ad.search_volume(
            "p53",
            DateBounds(date.fromisoformat("2024-01-15"), date.fromisoformat("2024-06-30")),
        )
        await ad.search_volume("p53", DateBounds())
    assert seen[0] == "(p53) AND FIRST_PDATE:[2024-01-15 TO 2024-06-30]"
    assert seen[1] == "p53"


# --- iter_ids ------------------------------------------------------------------


async def test_iter_ids_cursor_pagination():
    """cursorMark=* → nextCursorMark → 2nd page → done; sorted newest first; deduplicated."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        if "cursorMark" not in p:
            return _search_resp(3, [])  # count from the iteration (fallback, pageSize=1)
        if p["cursorMark"] == "*":
            return _search_resp(3, [{"id": "W2"}, {"id": "W1"}, {"id": "W3"}], cursor="C2")
        assert p["cursorMark"] == "C2"
        return _search_resp(3, [{"id": "W1"}, {"id": "W4"}])  # W1 en double

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        ids = [i async for i in ad.iter_ids("q", DateBounds(), limit=10)]
    assert ids == ["W2", "W1", "W3", "W4"]  # first-occurrence order, no duplicate
    assert calls[1]["cursorMark"] == "*"
    assert calls[1]["sort"] == "FIRST_PDATE desc"
    assert len(calls) == 3  # count + page 1 + page 2


async def test_iter_ids_falls_back_to_unsorted_when_sort_503s():
    """EBI returns 503 on sorted requests (incident 2026-10-31): fallback without sort, the import
    still goes through (arbitrary order); without 503, the sort is still requested."""
    calls: list[bool] = []  # True si l'appel portait sort

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        sorted_call = "sort" in p
        calls.append(sorted_call)
        if sorted_call:
            return httpx.Response(503, json={"errCode": 503, "errMsg": "unavailable"})
        if "cursorMark" not in p:
            return _search_resp(2, [])  # count
        return _search_resp(2, [{"id": "W1"}, {"id": "W2"}])

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        ids = [i async for i in ad.iter_ids("q", DateBounds(), limit=10)]
    assert ids == ["W1", "W2"]
    # count (no sort) → page 1 with sort (3 attempts) → fallback without sort
    assert calls == [False, True, True, True, False]


async def test_iter_ids_limit_and_cached_count():
    """``limit`` cap (newest first); the hidden count from ``search_volume`` reused
    (a single count call), otherwise a ``pageSize=1`` fallback."""
    n_counts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n_counts
        p = dict(request.url.params)
        if p.get("pageSize") == "1" and "cursorMark" not in p:
            n_counts += 1
            return _search_resp(10, [])
        if "cursorMark" in p:
            return _search_resp(10, [{"id": f"W{i}"} for i in range(5)])
        raise AssertionError(f"appel inattendu : {dict(p)}")

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        await ad.search_volume("q", DateBounds())  # remplit le cache
        ids = [i async for i in ad.iter_ids("q", DateBounds(), limit=5)]
    assert ids == ["W0", "W1", "W2", "W3", "W4"]
    assert n_counts == 1  # the search_volume count is reused (no 2nd count)

    async with _client(handler) as http:
        ad2 = EuropePMCAdapter(http, None)
        ids2 = [i async for i in ad2.iter_ids("q", DateBounds(), limit=5)]
    assert n_counts == 2  # iteration without a prior count → fallback
    assert ids2 == ["W0", "W1", "W2", "W3", "W4"]


# --- fetch_metadata ------------------------------------------------------------

NOTICE = {
    "id": "W123",
    "pmid": "34335492",
    "pmcid": "PMC12345",
    "doi": "10.1000/xyz.1",
    "title": "A title",
    "abstractText": "An abstract.",
    "language": "eng",
    "firstPublicationDate": "2024-05-01",
    "journalInfo": {"title": "A Journal"},
    "authorList": {"author": [{"fullName": "Doe J"}, {"fullName": "Roe R"}]},
    "fullTextIdList": {"fullTextId": ["PMC12345"]},
    "fullTextUrlList": {
        "fullTextUrl": [
            {},
            {"reader": {"url": "https://europepmc.org/article/MED/34335492"}},
        ]
    },
}


async def test_fetch_metadata_mapping():
    """``id`` → native_id, mapped fields; keys absent if the record lacks the value."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        assert p["query"] == 'id:("W123" OR "W456")'
        assert p["resultType"] == "core"
        assert p["pageSize"] == "2"
        return _search_resp(2, [NOTICE, {"id": "W456", "title": "Bare"}])

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        metas = await ad.fetch_metadata(["W123", "W456"])
    m = metas[0]
    assert m["native_id"] == "W123"
    assert (m["pmid"], m["pmcid"], m["doi"]) == ("34335492", "PMC12345", "10.1000/xyz.1")
    assert (m["title"], m["abstract"], m["journal"]) == ("A title", "An abstract.", "A Journal")
    assert m["authors"] == ["Doe J", "Roe R"]
    assert m["pub_date"] == "2024-05-01"
    assert m["language"] == "eng"
    assert m["fulltext_id"] == "PMC12345"
    assert m["fulltext_url"] == "https://europepmc.org/article/MED/34335492"
    assert m["fulltext_source"] == "europepmc"
    bare = metas[1]
    assert bare["native_id"] == "W456"
    for key in ("pmid", "pmcid", "doi", "fulltext_id", "fulltext_url", "authors", "pub_date"):
        assert key not in bare or bare.get(key) is None


async def test_fetch_metadata_chunking_and_empty():
    """250 ids → chunks of 100; ``[]`` → ``[]`` without a call."""
    n_calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        n_calls.append(len(q.split(" OR ")))
        return _search_resp(len(n_calls), [{"id": "W1"}])

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        await ad.fetch_metadata([f"W{i}" for i in range(250)])
    assert n_calls == [100, 100, 50]

    def boom(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("pas d'appel attendu")

    async with _client(boom) as http:
        assert await EuropePMCAdapter(http, None).fetch_metadata([]) == []


# --- fetch_fulltext ------------------------------------------------------------


async def test_fetch_fulltext_jats():
    """``/{id}/fullTextXML`` → parseur JATS existant : (methods, sections)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/europepmc/webservices/rest/W123/fullTextXML"
        return httpx.Response(200, text=JATS)

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        result = await ad.fetch_fulltext("W123")
    assert result is not None
    methods, sections = result
    assert methods == "Method text. Sub text."
    titles = [s["title"] for s in sections]
    assert "Introduction" in titles and "Methods" in titles and "Results" in titles


async def test_fetch_fulltext_unavailable_404():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with _client(handler) as http:
        assert await EuropePMCAdapter(http, None).fetch_fulltext("W123") is None


async def test_fetch_fulltext_size_limit():
    """XML > 10 MB → EpmcError (the XML is never returned nor logged)."""
    big = "<article>" + "x" * (11 * 1024 * 1024) + "</article>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    async with _client(handler) as http:
        with pytest.raises(EpmcError, match="too large"):
            await EuropePMCAdapter(http, None).fetch_fulltext("W123")


# --- throttle / retry / sanitized errors --------------------------------------


async def test_throttle_and_retry_503(monkeypatch):
    """Sleep between each call; 503 → retry backoff; persistent 503 → EpmcError."""
    sleeps: list[float] = []

    async def _rec(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.europepmc._sleep", _rec)
    states = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        states["n"] += 1
        if states["n"] == 1:
            return httpx.Response(503, text="rate limited")
        return _search_resp(1, [])

    async with _client(handler) as http:
        ad = EuropePMCAdapter(http, None)
        assert await ad.search_volume("q", DateBounds()) == 1
    assert states["n"] == 2  # 503 → retry → success
    assert sleeps[0] == _INTERVAL  # throttle avant l'appel
    assert len(sleeps) == 2 and all(s >= _INTERVAL for s in sleeps)  # throttle puis backoff

    async def always_503(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="rate limited")

    async with _client(always_503) as http:
        with pytest.raises(EpmcError, match="HTTP 503"):
            await EuropePMCAdapter(http, None).search_volume("q", DateBounds())


async def test_errors_sanitized():
    """403 → EpmcError('HTTP 403'): never the URL in the message (logs / job.error)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    async with _client(handler) as http:
        with pytest.raises(EpmcError) as exc:
            await EuropePMCAdapter(http, None).search_volume("q", DateBounds())
    assert str(exc.value) == "HTTP 403"
    assert BASE not in str(exc.value)


# --- enregistrement ------------------------------------------------------------


def test_registered_in_adapters_and_source_names():
    from app.tasks import ADAPTERS

    assert ADAPTERS["europepmc"] is EuropePMCAdapter
    assert source_name("europepmc") == "Europe PMC"
    assert source_label("europepmc", "W123") == "Europe PMC W123"
    assert source_label("europepmc", None) == "Europe PMC"
