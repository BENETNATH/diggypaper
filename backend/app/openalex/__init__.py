"""OpenAlex Works API client (source adapter n°3, spec 2026-10-29-sources-phase4a D1).

**Metadata-only** source: ``fetch_fulltext`` always returns ``None`` (→
``fulltext_note="metadata_only"`` on the pipeline side, never full text).

Usage-based since Feb 2026: without a key, shared free IP budget ($0.10/day,
``meta.notice`` in every response); with the secret config ``openalex_api_key``,
dedicated budget (``api_key`` parameter). Rate limit signaled by 429 → backoff
retry + **per-process** throttle at 1 call/s (no Redis limiter, same choice as
``app.pubmed`` / ``app.europepmc`` — one dedicated ingestion worker, one import
at a time per project).

``httpx.AsyncClient`` injected (not hardcoded) for mocking in tests; ``_sleep``
mocked in tests. Errors: clean ``OpenAlexError`` (WITHOUT the URL — the message
must never leak into ``job.error`` / logs).

Date bounds: in ``filter`` (``from_publication_date:…`` /
``to_publication_date:…``), **never** as query parameters (400 otherwise);
``meta.count`` is carried by every response (no separate count call). No
filter by work id (``filter=id:…`` → 400) → ``fetch_metadata`` does a
``GET /works/{id}`` per id.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from app.pubmed import _esearch_max
from app.sources import DateBounds

logger = logging.getLogger(__name__)

OALEX_BASE = "https://api.openalex.org/works"
_ID_PREFIX = "https://openalex.org/"
_DOI_PREFIX = "https://doi.org/"

#: Minimum interval between two OpenAlex calls (s): shared IP budget, keyless.
_INTERVAL = 1.0

#: Retry on transient error (429 / 5xx / timeout), simple backoff (s) — app.europepmc pattern.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Iteration page size (OpenAlex allows per_page ≤ 200).
_PAGE_SIZE = 200

#: Mocked in tests (no real sleep).
_sleep = asyncio.sleep


class OpenAlexError(Exception):
    """Clean OpenAlex error (message WITHOUT the URL) — same role as ``NCBIError``/``EpmcError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise OpenAlexError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict | None = None
) -> httpx.Response:
    """OpenAlex GET with retry on transient error (429 / 5xx / timeout), simple backoff."""
    for attempt in range(_RETRIES + 1):
        try:
            resp = await http.get(url, params=params)
        except httpx.TimeoutException:
            if attempt >= _RETRIES:
                raise OpenAlexError("timeout") from None
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if resp.status_code in (429, 503, 500, 502) and attempt < _RETRIES:
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        _raise_for_status(resp)
        return resp


def _bounds_filter(bornes: DateBounds) -> str | None:
    """Date bounds → ``filter`` term (OpenAlex does not accept them as query parameters)."""
    terms: list[str] = []
    if bornes.date_from is not None:
        terms.append(f"from_publication_date:{bornes.date_from:%Y-%m-%d}")
    if bornes.date_to is not None:
        terms.append(f"to_publication_date:{bornes.date_to:%Y-%m-%d}")
    return ",".join(terms) or None


def _bare_id(value: str | None) -> str | None:
    """Last path segment of an identifier URL (``ids.pmid`` / ``ids.pmcid``
    are URLs) → bare value for the identity and the DOI attach."""
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1] or None


def _rebuild_abstract(inverted: dict | None) -> str | None:
    """``abstract_inverted_index`` (word → 0-based positions) → text (sorted by position)."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for pos in idxs:
            positions.append((pos, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _parse_work(w: dict) -> dict:
    """One work record → pipeline meta dict (contract keys + ``fulltext_note``)."""
    meta: dict = {
        "native_id": (w.get("id") or "").removeprefix(_ID_PREFIX) or None,
        "title": w.get("display_name"),
        "abstract": _rebuild_abstract(w.get("abstract_inverted_index")),
        "language": w.get("language"),
        "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "pub_date": w.get("publication_date"),
        "fulltext_note": "metadata_only",  # D2: metadata-only source
    }
    doi = (w.get("doi") or "").removeprefix(_DOI_PREFIX)
    if doi:
        meta["doi"] = doi
    ids = w.get("ids") or {}
    for key in ("pmid", "pmcid"):
        if val := _bare_id(ids.get(key)):
            meta[key] = val
    authors = [
        (a.get("author") or {}).get("display_name")
        for a in (w.get("authorships") or [])
        if (a.get("author") or {}).get("display_name")
    ]
    if authors:
        meta["authors"] = authors
    return meta


class OpenAlexAdapter:
    """OpenAlex source adapter (n°3): ``SourceAdapter`` over the Works API.

    ``api_key`` (secret config ``openalex_api_key``): sent as the ``api_key``
    parameter when set (dedicated budget); without a key, free IP pool. The
    ``search_volume`` count is carried by every response (``meta.count``);
    ``iter_ids`` never makes another count call (page pagination, ``sort``
    most recent first).
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key

    def _params(self, query: str, bornes: DateBounds, extra: dict | None = None) -> dict:
        params: dict = {"search": query, **(extra or {})}
        if flt := _bounds_filter(bornes):
            params["filter"] = flt
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        b = bornes or DateBounds()
        await _throttle()
        params = self._params(query, b, {"per_page": 1})
        resp = await _get_with_retry(self._http, OALEX_BASE, params)
        return int((resp.json().get("meta") or {}).get("count") or 0)

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        b = bornes or DateBounds()
        limit = limit or _esearch_max()
        page = 1
        seen: dict[str, None] = {}
        while len(seen) < limit:
            per_page = min(_PAGE_SIZE, limit - len(seen))
            await _throttle()
            resp = await _get_with_retry(
                self._http,
                OALEX_BASE,
                self._params(
                    query,
                    b,
                    {
                        "sort": "publication_date:desc",
                        "page": page,
                        "per_page": per_page,
                    },
                ),
            )
            data = resp.json()
            count = int((data.get("meta") or {}).get("count") or 0)
            results = data.get("results") or []
            for w in results:
                if wid := (w.get("id") or "").removeprefix(_ID_PREFIX):
                    seen.setdefault(wid)
                    if len(seen) >= limit:
                        break
            # fully fetched page (page × per_page covers the count) or empty → done
            if not results or page * per_page >= count:
                break
            page += 1
        for nid in seen:
            yield nid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        metas: list[dict] = []
        for wid in ids:
            # No filter by work id (400): the unique object, one call per id.
            await _throttle()
            resp = await _get_with_retry(self._http, f"{OALEX_BASE}/{wid}", self._key_params())
            w = resp.json()
            if w.get("id"):
                metas.append(_parse_work(w))
        return metas

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        return None  # metadata-only source (→ fulltext_note "metadata_only")

    def _key_params(self) -> dict | None:
        return {"api_key": self._api_key} if self._api_key else None
