"""Europe PMC REST client (source adapter n°2, spec 2026-10-29-sources-phase3).

Free keyless API, weak and undocumented per-IP rate limit (503 after short
bursts, observed 2026-10-29 from a datacenter IP) → **per-process** throttle
at 1 call/s (no Redis limiter, same choice as ``app.pubmed``: deployment
constraint — one dedicated ingestion worker, one import at a time per
project).

``httpx.AsyncClient`` injected (not hardcoded) for mocking in tests; ``_sleep``
mocked in tests. Errors: clean ``EpmcError`` (WITHOUT the URL — the message must
never leak into ``job.error`` / logs), backoff retry on 503/timeout.

Full text: ``GET /{id}/fullTextXML`` returns JATS — the parser is reused
as-is from ``app.pubmed`` (``_extract_methods``/``_extract_sections``),
architecture constraint (no new parser).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx

from app.pubmed import _MAX_XML_BYTES, _esearch_max, _extract_methods, _extract_sections
from app.sources import DateBounds

logger = logging.getLogger(__name__)

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

#: Minimum interval between two EPMC calls (s): per-IP rate limit, keyless.
_INTERVAL = 1.0

#: Retry on transient error (503 / timeout), simple backoff (s) — app.pubmed pattern.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Iteration page size (EPMC allows pageSize ≤ 1000).
_PAGE_SIZE = 1000

#: Chunk size of ``fetch_metadata`` (query ``id:(… OR …)``).
_METADATA_CHUNK = 100

#: Mocked in tests (no real sleep).
_sleep = asyncio.sleep


class EpmcError(Exception):
    """Clean EPMC error (message WITHOUT the URL) — same role as ``NCBIError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise EpmcError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict | None = None
) -> httpx.Response:
    """EPMC GET with retry on transient error (503 / timeout), simple backoff."""
    for attempt in range(_RETRIES + 1):
        try:
            resp = await http.get(url, params=params)
        except httpx.TimeoutException:
            if attempt >= _RETRIES:
                raise EpmcError("timeout") from None
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if resp.status_code == 503 and attempt < _RETRIES:
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        _raise_for_status(resp)
        return resp


def _check_xml_size(resp: httpx.Response) -> None:
    if len(resp.content) > _MAX_XML_BYTES:
        raise EpmcError(f"Europe PMC XML payload too large: {len(resp.content)} bytes")


def _term_bounded(query: str, bornes: DateBounds) -> str:
    """Query bounded by first-publication date (``FIRST_PDATE``).

    The bounds are always AND-ed, even if the query already contains a date bound
    (same semantics as ``app.pubmed._term_bounded``); without bounds: raw query."""
    if bornes.date_from is None and bornes.date_to is None:
        return query
    lo = bornes.date_from or date(1800, 1, 1)
    hi = bornes.date_to or date.today() + timedelta(days=366)
    return f"({query}) AND FIRST_PDATE:[{lo:%Y-%m-%d} TO {hi:%Y-%m-%d}]"


def _first(lst: list | None) -> str | None:
    """First non-empty value of an EPMC list, else None."""
    return next((v for v in (lst or []) if v), None)


def _parse_result(r: dict) -> dict:
    """One ``resultType=core`` record → pipeline meta dict (spec D1 keys)."""
    fulltext_id = _first((r.get("fullTextIdList") or {}).get("fullTextId"))
    readers = (r.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    fulltext_url = next(
        ((u.get("reader") or {}).get("url") for u in readers if (u.get("reader") or {}).get("url")),
        None,
    )
    pub_date = (
        r.get("firstPublicationDate") or r.get("dateOfPublication") or str(r.get("pubYear"))
        if (r.get("firstPublicationDate") or r.get("dateOfPublication") or r.get("pubYear"))
        else None
    )
    author_list = (r.get("authorList") or {}).get("author", [])
    authors = [a.get("fullName") for a in author_list if a.get("fullName")]
    meta = {
        "native_id": r.get("id"),
        "title": r.get("title"),
        "abstract": r.get("abstractText"),
        "language": r.get("language"),
        "journal": (r.get("journalInfo") or {}).get("title"),
        "pub_date": pub_date,
        "fulltext_source": "europepmc",
        "fulltext_url": fulltext_url,
    }
    for key in ("pmid", "pmcid", "doi"):
        if r.get(key):
            meta[key] = r[key]
    if fulltext_id:
        meta["fulltext_id"] = fulltext_id
    if authors:
        meta["authors"] = authors
    return meta


class EuropePMCAdapter:
    """Europe PMC source adapter (n°2): ``SourceAdapter`` over the EPMC REST.

    ``api_key`` is ignored (keyless API) but captured for the common factory
    signature ``(client, api_key)``. The ``search_volume`` count is cached and
    reused by ``iter_ids`` (the pipeline always calls one before the other).
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key  # ignored: Europe PMC takes no key
        self._last_count: int | None = None

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        b = bornes or DateBounds()
        data = await _search(self._http, _term_bounded(query, b), 1, None, sort=False)
        self._last_count = int(data.get("hitCount") or 0)
        return self._last_count

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        b = bornes or DateBounds()
        if self._last_count is None:
            self._last_count = int(
                (await _search(self._http, _term_bounded(query, b), 1, None, sort=False)).get(
                    "hitCount"
                )
                or 0
            )
        # No cap → the import ceiling (``_esearch_max()``), like the PubMed adapter.
        limit = limit or _esearch_max()
        term = _term_bounded(query, b)
        cursor = "*"
        seen: dict[str, None] = {}
        use_sort = True
        while len(seen) < limit:
            try:
                data = await _search(
                    self._http, term, min(_PAGE_SIZE, limit - len(seen)), cursor, sort=use_sort
                )
            except EpmcError as exc:
                # 2026-10-31: EBI returned 503 only when ``sort`` was passed —
                # fallback without sorting (arbitrary order) so the import goes
                # through; sorting returns once EBI is OK.
                if use_sort and str(exc) == "HTTP 503":
                    use_sort = False
                    data = await _search(
                        self._http, term, min(_PAGE_SIZE, limit - len(seen)), cursor, sort=False
                    )
                else:
                    raise
            for r in (data.get("resultList") or {}).get("result") or []:
                if rid := r.get("id"):
                    seen.setdefault(rid)
                    if len(seen) >= limit:
                        break
            cursor = data.get("nextCursorMark")
            if not cursor or not (data.get("resultList") or {}).get("result"):
                break
        for nid in seen:
            yield nid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        metas: list[dict] = []
        for start in range(0, len(ids), _METADATA_CHUNK):
            chunk = ids[start : start + _METADATA_CHUNK]
            query = "id:(" + " OR ".join(f'"{i}"' for i in chunk) + ")"
            # ``sort=False``: order is irrelevant for an id-based fetch (and the 2026-10-31
            # EBI 503 hit every request carrying ``sort``).
            data = await _search(
                self._http, query, len(chunk), None, result_type="core", sort=False
            )
            for r in (data.get("resultList") or {}).get("result") or []:
                if r.get("id"):
                    metas.append(_parse_result(r))
        return metas

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        if not id:
            return None
        try:
            resp = await _get_with_retry(self._http, f"{EPMC_BASE}/{id}/fullTextXML")
        except EpmcError as exc:
            # no text (non-OA record) → "not available" on the pipeline side
            if str(exc) in ("HTTP 404", "HTTP 403"):
                return None
            raise
        _check_xml_size(resp)
        methods, sections = _extract_methods(resp.text), _extract_sections(resp.text)
        if methods is None and not sections:
            return None  # "not available" (no XML) → fulltext_note on the pipeline side
        return methods, sections


async def _search(
    http: httpx.AsyncClient,
    query: str,
    page_size: int,
    cursor: str | None,
    result_type: str = "lite",
    sort: bool = True,
) -> dict:
    """One ``/search`` request (throttle + retry) → the JSON (hitCount, resultList…).

    ``sort=False`` for count calls: sorting is useless (hitCount does not depend on
    order) and on 2026-10-31 the EPMC endpoint returned 503 only when ``sort`` was passed.
    """
    await _throttle()
    params: dict = {
        "query": query,
        "format": "json",
        "resultType": result_type,
        "pageSize": page_size,
    }
    if sort:
        params["sort"] = "FIRST_PDATE desc"
    if cursor is not None:
        params["cursorMark"] = cursor
    resp = await _get_with_retry(http, f"{EPMC_BASE}/search", params)
    return resp.json()
