"""HAL client (source adapter n°4, spec 2026-10-30-sources-phase4b).

Free keyless Solr JSON API (``api.archives-ouvertes.fr/search/``), no documented
rate limit → **per-process** throttle at 1 call/s (no Redis limiter, same choice
as ``app.pubmed`` / ``app.europepmc`` / ``app.openalex``: one dedicated ingestion
worker, one import at a time per project).

Full text: ``fileMain_s`` (main file URL, ~always PDF) — downloaded by
``app.fulltext._download_pdf`` (SSRF guard on every redirect hop, 25 MB cap,
``%PDF`` magic) and sectioned by ``pdf_to_sections`` (no new parser). Non-PDF
file (TEI/HTML) or refused download → "not available" (→ ``fulltext_note`` on the
pipeline side).

``httpx.AsyncClient`` injected (not hardcoded) for mocking in tests; ``_sleep``
mocked in tests. Errors: clean ``HalError`` (WITHOUT the URL — the message must
never leak into ``job.error`` / logs).

shortcut: no TEI/HTML extractor (rare files); to write if a corpus depends on it.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx

from app.fulltext import _download_pdf, pdf_to_sections
from app.pubmed import _esearch_max
from app.sources import DateBounds

logger = logging.getLogger(__name__)

HAL_BASE = "https://api.archives-ouvertes.fr/search/"

#: Fields requested from the API (iteration + metadata — the rest is discarded).
_FL = (
    "docid,halId_s,title_s,abstract_s,authFullName_s,language_s,doiId_s,"
    "journalTitle_s,producedDate_tdate,publicationDateY_i,fileMain_s"
)

#: "Most recent first" sort: production date descending + unique key as
#: tie-breaker (Solr requires it for a stable cursor).
_SORT = "producedDate_tdate desc,docid desc"

#: Minimum interval between two HAL calls (s): no documented limit,
#: politeness aligned with the other adapters.
_INTERVAL = 1.0

#: Retry on transient error (503 / timeout), simple backoff (s) — app.europepmc pattern.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Iteration page size (HAL allows rows ≤ 10,000; 1000 verified).
_PAGE_SIZE = 1000

#: Chunk size of ``fetch_metadata`` (query ``halId_s:(… OR …)``).
_METADATA_CHUNK = 100

#: Mocked in tests (no real sleep).
_sleep = asyncio.sleep


class HalError(Exception):
    """Clean HAL error (message WITHOUT the URL) — same role as ``NCBIError``/``EpmcError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise HalError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(http: httpx.AsyncClient, params: dict) -> httpx.Response:
    """GET ``/search/`` with retry on transient error (503 / timeout), simple backoff."""
    for attempt in range(_RETRIES + 1):
        try:
            resp = await http.get(HAL_BASE, params=params)
        except httpx.TimeoutException:
            if attempt >= _RETRIES:
                raise HalError("timeout") from None
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if resp.status_code == 503 and attempt < _RETRIES:
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        _raise_for_status(resp)
        return resp
    raise HalError("unreachable")  # pragma: no cover — la boucle ci-dessus couvre


def _fq_bounded(bornes: DateBounds) -> str | None:
    """Solr date filter on the production date (HAL analogue of PubMed's ``[dp]``).

    The bounds are always AND-ed (same semantics as
    ``app.pubmed._term_bounded``); without bounds → no ``fq``."""
    if bornes.date_from is None and bornes.date_to is None:
        return None
    lo = bornes.date_from or date(1800, 1, 1)
    hi = bornes.date_to or date.today() + timedelta(days=366)
    return f"producedDate_tdate:[{lo:%Y-%m-%d}T00:00:00Z TO {hi:%Y-%m-%d}T23:59:59Z]"


def _first(lst: list | None) -> str | None:
    """First non-empty value of a Solr list (``title_s``, ``abstract_s``…), else None."""
    return next((v for v in (lst or []) if v), None)


def _parse_doc(d: dict) -> dict:
    """One Solr record → pipeline meta dict (spec 4b D3 keys)."""
    meta = {
        "native_id": d.get("halId_s"),
        "title": _first(d.get("title_s")),
        "abstract": _first(d.get("abstract_s")),
        "language": _first(d.get("language_s")),
        "journal": d.get("journalTitle_s"),
        # Production date (ISO, truncated to the day); year only as fallback.
        "pub_date": (d.get("producedDate_tdate") or "")[:10]
        or (str(d["publicationDateY_i"]) if d.get("publicationDateY_i") else None),
        "authors": d.get("authFullName_s") or None,
        "fulltext_source": "hal",
    }
    if d.get("doiId_s"):
        meta["doi"] = d["doiId_s"]
    if d.get("fileMain_s"):
        # The main file URL IS the full-text identifier (the pipeline's
        # fulltext_id) and the readable link (fulltext_url).
        meta["fulltext_id"] = d["fileMain_s"]
        meta["fulltext_url"] = d["fileMain_s"]
    else:
        meta["fulltext_note"] = "hal_no_file"
    return meta


async def _search(
    http: httpx.AsyncClient, query: str, bornes: DateBounds, rows: int, cursor: str | None = None
) -> dict:
    """One ``/search/`` request (throttle + retry) → Solr's **root** JSON
    (``response`` = numFound/docs, ``nextCursorMark`` at the root level)."""
    await _throttle()
    params: dict = {"q": query, "wt": "json", "rows": rows, "fl": _FL, "sort": _SORT}
    if fq := _fq_bounded(bornes):
        params["fq"] = fq
    if cursor is not None:
        params["cursorMark"] = cursor
    resp = await _get_with_retry(http, params)
    return resp.json()


class HalAdapter:
    """HAL source adapter (n°4): ``SourceAdapter`` over the HAL Solr API.

    ``api_key`` is ignored (keyless API) but captured for the common factory
    signature ``(client, api_key)``. The ``search_volume`` count is cached and
    reused by ``iter_ids`` (the pipeline always calls one before the other).
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key  # ignoré : HAL ne prend pas de clé
        self._last_count: int | None = None

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        b = bornes or DateBounds()
        data = await _search(self._http, query, b, 0)
        self._last_count = int((data.get("response") or {}).get("numFound") or 0)
        return self._last_count

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        b = bornes or DateBounds()
        if self._last_count is None:
            r = await _search(self._http, query, b, 0)
            self._last_count = int((r.get("response") or {}).get("numFound") or 0)
        # Cap absent → the import ceiling (``_esearch_max()``), like the other adapters.
        limit = limit or _esearch_max()
        cursor = "*"
        seen: dict[str, None] = {}
        while len(seen) < limit:
            data = await _search(self._http, query, b, min(_PAGE_SIZE, limit - len(seen)), cursor)
            docs = (data.get("response") or {}).get("docs") or []
            for d in docs:
                if hid := d.get("halId_s"):
                    seen.setdefault(hid)
                    if len(seen) >= limit:
                        break
            cursor = data.get("nextCursorMark")
            if not cursor or not docs:
                break
        for nid in seen:
            yield nid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        metas: list[dict] = []
        for start in range(0, len(ids), _METADATA_CHUNK):
            chunk = ids[start : start + _METADATA_CHUNK]
            query = "halId_s:(" + " OR ".join(f'"{i}"' for i in chunk) + ")"
            data = await _search(self._http, query, DateBounds(), len(chunk))
            for d in (data.get("response") or {}).get("docs") or []:
                if d.get("halId_s"):
                    metas.append(_parse_doc(d))
        return metas

    async def fetch_fulltext(self, id: str) -> tuple[str | None, list[dict]] | None:
        """Main file URL (the record's ``fulltext_id``) → ``(None, sections)``.

        PDF only (shortcut D2): refused download (SSRF, >25 MB, non-PDF) or empty
        text → ``None`` (→ ``fulltext_note`` / completion by the other sources on
        the pipeline side)."""
        if not id:
            return None
        data = await _download_pdf(id, self._http)
        if data is None:
            return None
        sections = pdf_to_sections(data)
        if not sections:
            return None  # PDF sans texte extractible : « non disponible »
        return None, sections
