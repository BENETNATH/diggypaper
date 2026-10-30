"""Phase 4b : adapter HAL (spec 2026-10-30-sources-phase4b D1/D2) — 100 % offline."""

from datetime import date

import httpx
import pytest

from app.hal import HalAdapter, HalError
from app.sources import DateBounds

FILE_URL = "https://hal.science/hal-00478278/document"

DOC = {
    "docid": "478278",
    "halId_s": "hal-00478278",
    "title_s": ["A title"],
    "abstract_s": ["An abstract", ""],  # multivalué : la première non vide
    "language_s": ["en"],
    "authFullName_s": ["Alice", "Bob"],
    "journalTitle_s": "J Clin",
    "producedDate_tdate": "2008-11-18T00:00:00Z",
    "doiId_s": "10.1007/s10549-008-0235-7",
    "fileMain_s": FILE_URL,
}

DOC_NO_FILE = {k: v for k, v in DOC.items() if k != "fileMain_s"} | {
    "docid": "4167683",
    "halId_s": "hal-04167683",
    "doiId_s": None,
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _resp(num_found: int, docs: list[dict], cursor: str | None = "AoIx") -> httpx.Response:
    body: dict = {"response": {"numFound": num_found, "docs": docs}}
    if cursor is not None:
        body["nextCursorMark"] = cursor
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.hal._sleep", _noop)


# --- search_volume ---------------------------------------------------------------


async def test_search_volume_params_and_count():
    """``rows=0`` → ``numFound`` ; ``q``/``wt``/``sort`` posés, pas de ``fq`` sans bornes."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.archives-ouvertes.fr"
        assert request.url.path == "/search/"
        seen.update(dict(request.url.params))
        return _resp(42, [], cursor=None)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        assert await ad.search_volume("cancer", DateBounds()) == 42
    assert seen["q"] == "cancer"
    assert seen["wt"] == "json" and seen["rows"] == "0"
    assert seen["sort"] == "producedDate_tdate desc,docid desc"
    assert "fq" not in seen and "cursorMark" not in seen


async def test_search_volume_date_bounds_fq():
    """Bornes → ``fq`` ``producedDate_tdate:[… TO …]`` (toujours ANDées, à la PubMed)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return _resp(7, [], cursor=None)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        assert await ad.search_volume("q", DateBounds(date_from=date(2024, 1, 1))) == 7
    assert seen["fq"].startswith("producedDate_tdate:[2024-01-01T00:00:00Z TO ")


# --- iter_ids --------------------------------------------------------------------


async def test_iter_ids_cursor_pagination_and_limit():
    """``cursorMark=*`` puis ``nextCursorMark`` ; cap ``limit`` ; les plus récents
    d'abord via ``sort`` (porté par chaque requête)."""
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursors.append(request.url.params.get("cursorMark"))
        rows = int(request.url.params["rows"])
        if len(cursors) == 1:  # page 1 : 2 docs, doublon ignoré
            docs = [{"halId_s": "hal-1"}, {"halId_s": "hal-1"}, {"halId_s": "hal-2"}]
            return _resp(9, docs, "c1")
        assert rows == 1  # page 2 : rows = limit - len(seen)
        return _resp(9, [{"halId_s": "hal-3"}], cursor=None)  # fin (plus de curseur)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        ad._last_count = 9  # volume déjà fait (le pipeline appelle l'un avant l'autre)
        ids = [nid async for nid in ad.iter_ids("q", DateBounds(), limit=3)]
    assert ids == ["hal-1", "hal-2", "hal-3"]
    assert cursors == ["*", "c1"]


async def test_iter_ids_volume_not_preloaded():
    """Sans ``search_volume`` préalable : le count est fait à la volée puis paginé."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["rows"] == "0":
            return _resp(1, [], cursor=None)
        return _resp(1, [{"halId_s": "hal-1"}], cursor=None)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        assert [nid async for nid in ad.iter_ids("q", DateBounds())] == ["hal-1"]


# --- fetch_metadata --------------------------------------------------------------


async def test_fetch_metadata_or_query_and_parsing():
    """``halId_s:("…" OR …)`` + ``rows`` ; parsing : listes → première valeur,
    DOI, pub_date produite, ``fulltext_id``/``fulltext_url`` = ``fileMain_s``."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return _resp(2, [DOC, DOC_NO_FILE], cursor=None)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        metas = await ad.fetch_metadata(["hal-00478278", "hal-04167683"])
    assert seen["q"] == 'halId_s:("hal-00478278" OR "hal-04167683")'
    assert seen["rows"] == "2"
    by_id = {m["native_id"]: m for m in metas}
    m = by_id["hal-00478278"]
    assert m["title"] == "A title" and m["abstract"] == "An abstract"
    assert m["language"] == "en" and m["journal"] == "J Clin"
    assert m["pub_date"] == "2008-11-18" and m["authors"] == ["Alice", "Bob"]
    assert m["doi"] == "10.1007/s10549-008-0235-7"
    assert m["fulltext_id"] == FILE_URL and m["fulltext_url"] == FILE_URL
    assert m["fulltext_source"] == "hal"
    # notice sans fichier : note explicite, pas de fulltext_id
    assert by_id["hal-04167683"]["fulltext_note"] == "hal_no_file"
    assert "fulltext_id" not in by_id["hal-04167683"]


async def test_fetch_metadata_year_fallback_and_chunks():
    """``producedDate_tdate`` absent → année ``publicationDateY_i`` en secours ;
    > 100 identifiants → tranches de 100 (100 + 50)."""

    def handler(request: httpx.Request) -> httpx.Response:
        rows = int(request.url.params["rows"])
        doc = {"docid": "1", "halId_s": "hal-1", "title_s": ["T"], "publicationDateY_i": 2022}
        return _resp(rows, [doc] * rows, cursor=None)

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        metas = await ad.fetch_metadata(["hal-1"] * 150)
    assert len(metas) == 150 and all(m["pub_date"] == "2022" for m in metas)


async def test_fetch_metadata_empty_ids():
    async with _client(lambda r: _resp(0, [])) as http:
        assert await HalAdapter(http, None).fetch_metadata([]) == []


def test_parse_doc_year_fallback():
    """(hors async) année seule quand la date de production manque."""
    from app.hal import _parse_doc

    assert _parse_doc({"halId_s": "h", "publicationDateY_i": 2022})["pub_date"] == "2022"
    assert _parse_doc({"halId_s": "h"})["pub_date"] is None


# --- fetch_fulltext (PDF via l'extracteur existant, D2) ---------------------------


async def test_fetch_fulltext_pdf(monkeypatch):
    """PDF téléchargé + sectionné → ``(None, sections)`` ; l'URL passe telle quelle
    à ``_download_pdf`` (garde SSRF / 25 Mo / %PDF dans app.fulltext)."""
    calls: list[str] = []

    async def _dl(url, http):
        calls.append(url)
        return b"%PDF-fake"

    monkeypatch.setattr("app.hal._download_pdf", _dl)
    sections = [{"title": "Methods", "kind": "methods", "text": "Cohort of 200 patients."}]

    def _pdf_sections(data: bytes) -> list[dict]:
        return sections if data == b"%PDF-fake" else []

    monkeypatch.setattr("app.hal.pdf_to_sections", _pdf_sections)

    async with _client(lambda r: _resp(0, [])) as http:
        ad = HalAdapter(http, None)
        assert await ad.fetch_fulltext(FILE_URL) == (None, sections)
        assert calls == [FILE_URL]


async def test_fetch_fulltext_unavailable(monkeypatch):
    """Échec de téléchargement (non-PDF, SSRF, >25 Mo) → ``None`` ; PDF sans
    texte extractible → ``None`` ; URL vide → ``None`` sans appel."""

    async def _dl(url, http):
        return None

    monkeypatch.setattr("app.hal._download_pdf", _dl)
    monkeypatch.setattr("app.hal.pdf_to_sections", lambda data: [])

    async with _client(lambda r: _resp(0, [])) as http:
        ad = HalAdapter(http, None)
        assert await ad.fetch_fulltext(FILE_URL) is None  # refusé
        assert await ad.fetch_fulltext("") is None  # vide : pas d'appel


async def test_fetch_fulltext_empty_sections(monkeypatch):
    """PDF mais aucune section extraite → ``None`` (pas de fulltext_source sans texte)."""

    async def _dl(url, http):
        return b"%PDF-fake"

    monkeypatch.setattr("app.hal._download_pdf", _dl)
    monkeypatch.setattr("app.hal.pdf_to_sections", lambda data: [])

    async with _client(lambda r: _resp(0, [])) as http:
        assert await HalAdapter(http, None).fetch_fulltext(FILE_URL) is None


# --- erreurs / retry (pattern app.europepmc) -------------------------------------


async def test_error_400_sanitized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    async with _client(handler) as http:
        with pytest.raises(HalError) as exc:
            await HalAdapter(http, None).search_volume("q", DateBounds())
        assert str(exc.value) == "HTTP 400"  # pas d'URL dans le message


async def test_retry_503_then_ok():
    """503 → backoff puis retry (``_sleep`` mocké) ; le 2ᵉ appel réussit."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _resp(5, [], cursor=None) if calls["n"] > 1 else httpx.Response(503, json={})

    async with _client(handler) as http:
        ad = HalAdapter(http, None)
        assert await ad.search_volume("q", DateBounds()) == 5
    assert calls["n"] == 2


async def test_timeout_raises_clean():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    async with _client(handler) as http:
        with pytest.raises(HalError, match="timeout"):
            await HalAdapter(http, None).search_volume("q", DateBounds())


# --- registre --------------------------------------------------------------------


def test_registered_in_adapters():
    from app.tasks import ADAPTERS

    assert ADAPTERS["hal"] is HalAdapter
