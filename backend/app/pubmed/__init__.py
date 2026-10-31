"""NCBI E-utilities client (PubMed/PMC) — Module 2.

``httpx.AsyncClient`` is injected (not hardcoded) to allow mocking in
tests. ``_sleep`` is mocked in tests (no real sleep).

NCBI rate limiting (M5): 3 req/s without an API key, 10 req/s with — the
throttle is **per process** (``asyncio.sleep`` between calls, no Redis limiter:
the ingestion volume is low, a shared limiter would be over-engineering).
Consequence in multi-worker production: each Celery worker has its own
counter → **mandatory deployment constraint (M-3): a single worker dedicated
to ingestion** (``celery -A app.celery_app worker -c 1``) to stay under the
NCBI ceiling. The application rate limits (M-1/M-2) + the "one import per
project" guard (M-2) mitigate application DoS; no Redis limiter.

Errors (C-1): NCBI HTTP errors (>= 400) and timeouts raise
``NCBIError`` (clean message, WITHOUT the URL or the API key) — not
``httpx.HTTPStatusError`` (whose str() contains the full URL with
``api_key=…``). This prevents the NCBI key leaking via ``job.error`` / logs.

``oa.fcgi`` (``fetch_methods``) is not an E-utility: the 1/3 s throttle is
kept out of caution (m12).

Retry (m5): 2 retries with simple backoff on transient errors (429 / 5xx /
timeout) for esearch/efetch, honoring ``Retry-After`` on a 429 (NCBI sends it
when the rate exceeds the ceiling: 3 req/s keyless, 10 with). Without this
retry, a burst (several processes) would fail the batch instead of slowing down.
No retry on parsing errors (parsing is done by the caller, after the
response).

Size limit (I5): NCBI XML payloads > 10 MB are refused (defense in
depth — the input is NCBI over HTTPS, not user-controlled; the size
limit suffices, no defusedxml).
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx

from app.sources import DateBounds

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

#: Minimum interval between two NCBI calls (s): 3 req/s keyless, 10 req/s with key.
_INTERVAL_NO_KEY = 1 / 3
_INTERVAL_WITH_KEY = 0.1


#: Articles per import ceiling (IMPORT_MAX_ARTICLES, default 5000; 0 = unlimited).
UNLIMITED = 10**9


def _esearch_max() -> int:
    from app.core.config import get_settings

    n = get_settings().import_max_articles
    return n if n > 0 else UNLIMITED


#: An esearch query never returns more than 10,000 PubMed identifiers (NCBI limit,
#: even with ``retstart``): beyond that, split by publication date (cf. ``fetch_all_pmids``).
PUBMED_WINDOW = 9999


#: m5: retry on transient error (429 / 5xx / timeout), simple backoff (s).
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Retried HTTP codes (429 = rate exceeded: the "too fast" case, not a fatal error).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

#: ``Retry-After`` wait ceiling (s): a worker does not sleep an hour over a header.
_RETRY_AFTER_MAX = 30.0

#: I5: NCBI XML payload size limit (defense in depth): 10 MB.
_MAX_XML_BYTES = 10 * 1024 * 1024

#: Mocked in tests (no real sleep).
_sleep = asyncio.sleep

#: Section titles recognized as "Methods" (case-insensitive).
_METHODS_TITLES = {"methods", "materials and methods", "méthodes"}


class NCBIError(Exception):
    """C-1: clean NCBI error (message WITHOUT the URL or the API key).

    Replaces ``httpx.HTTPStatusError`` (whose str() contains the full URL
    with ``api_key=…``) to prevent the NCBI key leaking via ``job.error``
    and the server logs.
    """


def _raise_for_status(resp: httpx.Response) -> None:
    """C-1: raises NCBIError (WITHOUT the URL) instead of httpx.HTTPStatusError."""
    if resp.status_code >= 400:
        raise NCBIError(f"HTTP {resp.status_code}")


async def _throttle(api_key: str | None) -> None:
    """Spaces out NCBI calls (3 req/s keyless, 10 req/s with key)."""
    await _sleep(_INTERVAL_WITH_KEY if api_key else _INTERVAL_NO_KEY)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict, *, retries: int = _RETRIES
) -> httpx.Response:
    """NCBI GET with retry on transient errors (m5): 429 / 5xx / timeout.

    ``retries`` retries with simple backoff (``_sleep``, mocked in tests); on 429,
    ``Retry-After`` (in seconds) is honored, capped at ``_RETRY_AFTER_MAX``.
    No retry on parsing errors (parsing is done by the caller).
    C-1: persistent timeout → NCBIError('timeout'); persistent HTTP error →
    NCBIError('HTTP <code>') (WITHOUT the URL or the key).
    """
    for attempt in range(retries + 1):
        try:
            resp = await http.get(url, params=params)
        except httpx.TimeoutException:
            if attempt >= retries:
                raise NCBIError("timeout") from None
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if resp.status_code in _RETRY_STATUS and attempt < retries:
            wait = _RETRY_BACKOFF_SECONDS * (attempt + 1)
            if (after := resp.headers.get("Retry-After", "")).isdigit():
                wait = min(float(after), _RETRY_AFTER_MAX)
            await _sleep(wait)
            continue
        _raise_for_status(resp)
        return resp


def _check_xml_size(resp: httpx.Response) -> None:
    """I5: refuses NCBI XML payloads > 10 MB (defense in depth)."""
    if len(resp.content) > _MAX_XML_BYTES:
        raise ValueError(f"NCBI XML payload too large: {len(resp.content)} bytes")


def _term_bounded(query: str, date_from: date | None, date_to: date | None) -> str:
    """NCBI term bounded by publication date ([dp]); raw query if no bound.

    The bounds are always AND-ed, even if the query already contains ``[dp]``
    (intersection, like the bisection slices: result independent of the volume)."""
    if date_from is None and date_to is None:
        return query
    lo = date_from or date(1800, 1, 1)
    hi = date_to or date.today() + timedelta(days=366)
    return f'({query}) AND ("{lo:%Y/%m/%d}"[dp] : "{hi:%Y/%m/%d}"[dp])'


def _esearch_params(query: str, retmax: int, api_key: str | None) -> dict:
    params = {"db": "pubmed", "term": query, "retmax": retmax, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    return params


async def search_volume(query: str, api_key: str | None, http: httpx.AsyncClient) -> int:
    """PubMed result volume (esearch ``retmax=0``) → count (int)."""
    await _throttle(api_key)
    resp = await _get_with_retry(
        http, f"{EUTILS_BASE}/esearch.fcgi", _esearch_params(query, 0, api_key)
    )
    return int(resp.json()["esearchresult"]["count"])


async def explain_query(query: str, api_key: str | None, http: httpx.AsyncClient) -> dict:
    """How PubMed understands the query: {count, translation, not_found}.

    ``translation`` = the query rewritten by PubMed (MeSH terms, fields); ``not_found`` =
    terms or fields it does not know (invented MeSH, typo): signal of a wrong query.
    """
    await _throttle(api_key)
    resp = await _get_with_retry(
        http, f"{EUTILS_BASE}/esearch.fcgi", _esearch_params(query, 0, api_key)
    )
    r = resp.json()["esearchresult"]
    errors = r.get("errorlist") or {}
    warnings = r.get("warninglist") or {}
    not_found = [
        *errors.get("phrasesnotfound", []),
        *errors.get("fieldsnotfound", []),
        *warnings.get("phrasesignored", []),
        *warnings.get("quotedphrasesnotfound", []),
        *warnings.get("outputmessage", []),
    ]
    return {
        "count": int(r.get("count") or 0),
        "translation": r.get("querytranslation") or "",
        "not_found": [str(x) for x in not_found][:20],
    }


async def fetch_pmids(
    query: str, api_key: str | None, http: httpx.AsyncClient, retmax: int | None = None
) -> list[str]:
    """PMIDs of a query (esearch): at most ``retmax`` (default: the import ceiling) and never
    more than the NCBI limit (``PUBMED_WINDOW`` identifiers per query)."""
    await _throttle(api_key)
    params = _esearch_params(query, min(retmax or _esearch_max(), PUBMED_WINDOW), api_key)
    resp = await _get_with_retry(http, f"{EUTILS_BASE}/esearch.fcgi", params)
    return list(resp.json()["esearchresult"]["idlist"])


async def fetch_all_pmids(
    query: str,
    api_key: str | None,
    http: httpx.AsyncClient,
    count: int,
    limit: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[str]:
    """All PMIDs of a query, even beyond 10,000 results.

    NCBI delivers only 9,999 identifiers per query: a bigger query is split into
    publication-date slices (bisection while a slice exceeds the limit), most
    recent first; duplicates (print / electronic dates) are removed. ``limit``
    caps the total (the most recent are kept).

    Order: beyond the window, "most recent first" holds at the slice level
    (the bisection visits the most recent slices first, NCBI not sorting by date
    within a slice); a slice's identifiers are inserted by reversing the order
    returned by esearch, which is ascending by date on narrow windows. Below the
    window (a single esearch), the returned order is NCBI's.

    ``date_from``/``date_to`` (user bounds, optional) bound the query by
    publication date (``[dp]``); absent: unchanged behavior.
    """
    limit = limit or _esearch_max()
    term = _term_bounded(query, date_from, date_to)
    if count <= PUBMED_WINDOW:
        # A single esearch: the order is NCBI's; the cap applies on the NCBI side (retmax).
        return await fetch_pmids(term, api_key, http, min(count, limit))
    found: dict[str, None] = {}

    async def walk(lo: date, hi: date) -> None:
        if len(found) >= limit:
            return
        term = f'({query}) AND ("{lo:%Y/%m/%d}"[dp] : "{hi:%Y/%m/%d}"[dp])'
        n = await search_volume(term, api_key, http)
        if n == 0:
            return
        if n <= PUBMED_WINDOW or lo == hi:
            if n > PUBMED_WINDOW:  # plus de 9 999 articles un même jour : improbable
                logger.warning("PubMed: %d résultats le %s, tronqués", n, lo)
            for pmid in reversed(await fetch_pmids(term, api_key, http, PUBMED_WINDOW)):
                found.setdefault(pmid)  # inversé : esearch renvoie les plus anciens d'abord
            return
        mid = lo + (hi - lo) // 2
        await walk(mid + timedelta(days=1), hi)  # les plus récents d'abord
        await walk(lo, mid)

    # User bounds (optional); an absent bound falls back to the current default.
    await walk(date_from or date(1800, 1, 1), date_to or date.today() + timedelta(days=366))
    if len(found) < count:
        logger.warning(
            "PubMed: %d identifiants sur %d (articles sans date de publication)", len(found), count
        )
    return list(found)[:limit]


async def fetch_metadata(
    pmids: list[str], api_key: str | None, http: httpx.AsyncClient
) -> list[dict]:
    """PubMed metadata (efetch XML): pmid, pmcid?, doi?, title, abstract, language."""
    if not pmids:
        return []
    await _throttle(api_key)
    params = {"db": "pubmed", "retmode": "xml", "id": ",".join(pmids)}
    if api_key:
        params["api_key"] = api_key
    resp = await _get_with_retry(http, f"{EUTILS_BASE}/efetch.fcgi", params)
    _check_xml_size(resp)
    return _parse_pubmed_xml(resp.text)


async def fetch_methods(pmcid: str | None, http: httpx.AsyncClient) -> str | None:
    """Methods section of the PMC full text (efetch ``db=pmc``, JATS) → text.

    ``None`` if no pmcid or no Methods section.
    The ``db=pmc`` efetch is an E-utility → NCBI throttle (3 req/s keyless).
    (The old ``oa.fcgi`` is obsolete: systematic 404, observed 2026-10-12.)
    """
    xml = await _fetch_pmc_xml(pmcid, http)
    return _extract_methods(xml) if xml else None


async def fetch_fulltext(
    pmcid: str | None, http: httpx.AsyncClient
) -> tuple[str | None, list[dict]]:
    """PMC full text in a single call: (Methods text, sections [{title, kind, text}])."""
    xml = await _fetch_pmc_xml(pmcid, http)
    if not xml:
        return None, []
    return _extract_methods(xml), _extract_sections(xml)


async def _fetch_pmc_xml(pmcid: str | None, http: httpx.AsyncClient) -> str | None:
    if not pmcid:
        return None
    await _throttle(None)
    resp = await _get_with_retry(
        http, f"{EUTILS_BASE}/efetch.fcgi", {"db": "pmc", "retmode": "xml", "id": pmcid}
    )
    _check_xml_size(resp)
    return resp.text


# --- XML parsing (xml.etree.ElementTree, no namespace) ---


def _text(el: ET.Element, xpath: str) -> str | None:
    node = el.find(xpath)
    if node is None:
        return None
    value = (node.text or "").strip()
    return value or None


def _abstract(article: ET.Element) -> str | None:
    parts = [
        (t.text or "").strip()
        for t in article.findall("MedlineCitation/Article/Abstract/AbstractText")
    ]
    text = " ".join(p for p in parts if p).strip()
    return text or None


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def _pub_date(article: ET.Element) -> str | None:
    """Partial ISO publication date: electronic date, else issue date."""
    for base in (
        "MedlineCitation/Article/ArticleDate",
        "MedlineCitation/Article/Journal/JournalIssue/PubDate",
    ):
        year = _text(article, f"{base}/Year")
        if not year:
            raw = _text(article, f"{base}/MedlineDate")  # ex. « 2024 Jan-Feb »
            m = re.match(r"(\d{4})(?:\s+([A-Za-z]{3}))?", raw or "")
            if not m:
                continue
            year, month = m.group(1), (m.group(2) or "").lower()
            return f"{year}-{_MONTHS[month]:02d}" if month in _MONTHS else year
        month = (_text(article, f"{base}/Month") or "").strip().lower()[:3]
        mm = (
            f"{int(month):02d}"
            if month.isdigit()
            else (f"{_MONTHS[month]:02d}" if month in _MONTHS else "")
        )
        day = _text(article, f"{base}/Day")
        return "-".join(
            x for x in (year, mm, f"{int(day):02d}" if mm and day and day.isdigit() else "") if x
        )
    return None


def _authors(article: ET.Element) -> list[str]:
    out = []
    for a in article.findall("MedlineCitation/Article/AuthorList/Author"):
        last, ini = _text(a, "LastName"), _text(a, "Initials")
        name = f"{last} {ini}".strip() if last else _text(a, "CollectiveName")
        if name:
            out.append(name)
    return out


def _parse_pubmed_xml(xml_text: str) -> list[dict]:
    """One dict per ``PubmedArticle``: pmid, pmcid?, doi?, title, abstract, language."""
    # ElementTree resolves no external entities (no XXE); payload size is bounded.
    root = ET.fromstring(xml_text)  # nosec B314
    articles: list[dict] = []
    for article in root.iter("PubmedArticle"):
        articles.append(
            {
                # PMID is a direct child of MedlineCitation (not inside Article).
                "pmid": _text(article, "MedlineCitation/PMID"),
                # ArticleIdList is in PubmedData (sibling of MedlineCitation).
                "pmcid": _text(article, "PubmedData/ArticleIdList/ArticleId[@IdType='pmc']"),
                "doi": _text(article, "MedlineCitation/Article/ELocationID[@EIdType='doi']")
                or _text(article, "PubmedData/ArticleIdList/ArticleId[@IdType='doi']"),
                "title": _text(article, "MedlineCitation/Article/ArticleTitle"),
                "abstract": _abstract(article),
                "language": _text(article, "MedlineCitation/Article/Language"),
                "journal": _text(article, "MedlineCitation/Article/Journal/Title")
                or _text(article, "MedlineCitation/Article/Journal/ISOAbbreviation"),
                "pub_date": _pub_date(article),
                "authors": _authors(article),
                "pub_types": [
                    (t.text or "").strip()
                    for t in article.findall(
                        "MedlineCitation/Article/PublicationTypeList/PublicationType"
                    )
                    if t.text
                ],
            }
        )
    return articles


def _extract_methods(xml_text: str) -> str | None:
    """Text of the first JATS section titled Methods / Materials and Methods / Méthodes.

    The PMC full text is in JATS (lowercase tags: ``sec``, ``title``, ``p``) —
    not the MEDLINE format (``Sec``/``Title``). Nested sub-sections are
    included (``sec.iter('p')`` walks the whole tree).
    """
    # ElementTree resolves no external entities (no XXE); payload size is bounded.
    root = ET.fromstring(xml_text)  # nosec B314
    for sec in root.iter("sec"):
        title_el = sec.find("title")
        if title_el is None:
            continue
        if (title_el.text or "").strip().lower() not in _METHODS_TITLES:
            continue
        parts = [(el.text or "").strip() for el in sec.iter("p") if el.text and el.text.strip()]
        text = " ".join(p for p in parts if p).strip()
        return text or None
    return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _plain(el: ET.Element) -> str:
    """Text of a JATS element without formula noise: LaTeX (``tex-math``, including the
    ``\\documentclass…`` preamble) is ignored; an out-of-text formula becomes "[formula]";
    inline MathML (p < 0.05, x²…) is kept."""
    out = [el.text or ""]
    for child in el:
        name = _local(child.tag)
        if name == "disp-formula":
            out.append(" [formula] ")
        elif name != "tex-math":
            out.append(_plain(child))
        out.append(child.tail or "")
    return "".join(out)


def _extract_sections(xml_text: str) -> list[dict]:
    """First-level sections of the JATS ``body``: [{title, kind, text}]."""
    from app.article_text import classify_title

    # ElementTree resolves no external entities (no XXE); payload size is bounded.
    root = ET.fromstring(xml_text)  # nosec B314
    body = root.find(".//body")
    if body is None:
        return []
    out: list[dict] = []
    for sec in body.findall("sec"):
        title_el = sec.find("title")
        title = _plain(title_el).strip() if title_el is not None else ""
        paras = [re.sub(r"\s+", " ", _plain(p)).strip() for p in sec.iter("p")]
        text = " ".join(p for p in paras if p)
        if text:
            out.append({"title": title, "kind": classify_title(title), "text": text})
    return out


# --- Phase 1 : contrat SourceAdapter (spec 2026-10-28) -----------------------


class PubMedAdapter:
    """PubMed source adapter (n°1): the module's functions behind the
    ``SourceAdapter`` contract (thin delegation — the logic is moved, not rewritten).

    ``http`` and ``api_key`` are captured once in ``__init__`` and reused
    by every operation. The ``search_volume`` count is cached and reused
    by ``iter_ids`` (the pipeline always calls ``search_volume`` before
    ``iter_ids`` with the same ``(query, bounds)`` → no 3rd esearch); an
    ``iter_ids`` without a prior count (outside the pipeline) does the fallback (1
    extra esearch).
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str | None) -> None:
        self._http = http
        self._api_key = api_key
        self._last_count: int | None = None  # cache search_volume → réutilisé par iter_ids

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        b = bornes or DateBounds()
        self._last_count = await search_volume(
            _term_bounded(query, b.date_from, b.date_to), self._api_key, self._http
        )
        return self._last_count

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        b = bornes or DateBounds()
        count = self._last_count
        if count is None:
            count = await search_volume(
                _term_bounded(query, b.date_from, b.date_to), self._api_key, self._http
            )
        # fetch_all_pmids absorbs the paging/bisection: observable behavior
        # identical (same order "most recent first", same deduplication, cap).
        pmids = await fetch_all_pmids(
            query, self._api_key, self._http, count, limit, b.date_from, b.date_to
        )
        for pmid in pmids:
            yield pmid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        metas = await fetch_metadata(ids, self._api_key, self._http)
        for m in metas:
            m["native_id"] = m.get("pmid")  # contrat : native_id (PubMed = PMID)
        return metas

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        if not id:
            return None
        methods, sections = await fetch_fulltext(id, self._http)
        if methods is None and not sections:
            return None  # « non disponible » (pas de XML PMC) → fulltext_note
        return methods, sections
