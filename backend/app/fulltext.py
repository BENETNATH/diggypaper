"""Full text outside PMC: legal open sources (open access, preprints).

Order: Europe PMC JATS full text (by PMCID, found via the PMID if needed) → open
PDFs listed by Unpaywall then OpenAlex (editor versions, accepted manuscript,
bioRxiv/medRxiv/arXiv preprints). Articles truly paywalled with no open copy stay
without full text (abstract only); no pirate source.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import unicodedata
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.article_text import classify_title
from app.db.models import ArticleFile
from app.model_access import check_public_url

logger = logging.getLogger(__name__)

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX = "https://api.openalex.org/works"
UNPAYWALL = "https://api.unpaywall.org/v2"

MIN_CHARS = 1500  # below: landing page / paywall, not an article
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 80
MAX_PDF_TRIES = 4
MAX_REDIRECTS = 5
_VERSIONS = {
    "submittedVersion": "preprint",
    "acceptedVersion": "accepted",
    "publishedVersion": "published",
}
_UA = {"User-Agent": "Diggypaper/1.0 (systematic review; open-access full text)"}

_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+|[IVX]+\.\s+)?"
    r"(abstract|introduction|background|materials?\s+and\s+methods?|methods?|"
    r"patients\s+and\s+methods|experimental\s+procedures|results?(?:\s+and\s+discussion)?|"
    r"discussion|conclusions?|references|acknowledg\w+|bibliography)\s*:?\s*$",
    re.IGNORECASE,
)
_STOP = ("references", "bibliography", "acknowledg")


def pdf_to_sections(data: bytes) -> list[dict]:
    """PDF text → [{title, kind, text}] split at the usual headings (IMRaD)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    lines: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        # NUL is forbidden in Postgres JSONB ("unsupported Unicode escape sequence")
        lines += (page.extract_text() or "").replace("\x00", "").splitlines()
    sections: list[dict] = []
    title, buf = "", []

    def flush() -> None:
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if text:
            sections.append(
                {"title": title or "Full text", "kind": classify_title(title), "text": text}
            )

    for raw in lines:
        line = raw.strip()
        m = _HEADING.match(line)
        if not m:
            buf.append(line[:-1] if line.endswith("-") else line + " ")
            continue
        flush()
        buf = []
        if m.group(1).lower().startswith(_STOP):
            return sections  # references / acknowledgments: end of the body
        title = line
    flush()
    return sections


async def _get_json(http: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    try:
        resp = await http.get(url, params=params, headers=_UA)
        return resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def _epmc_xml(pmcid: str, http: httpx.AsyncClient) -> list[dict]:
    from app.pubmed import _extract_sections

    try:
        resp = await http.get(f"{EPMC}/{pmcid}/fullTextXML", headers=_UA)
        if resp.status_code != 200:
            return []
        return _extract_sections(resp.text)
    except Exception:  # noqa: BLE001 - network or malformed XML: next source
        return []


async def _epmc_ids(
    pmid: str | None, http: httpx.AsyncClient, doi: str | None = None
) -> tuple[str | None, str | None]:
    """(pmcid, doi) known to Europe PMC for this PMID (or, without PMID, this DOI)."""
    if pmid:
        query = f"EXT_ID:{pmid} AND SRC:MED"
    elif doi and '"' not in doi:
        query = f'DOI:"{doi}"'
    else:
        return None, None
    data = await _get_json(
        http, f"{EPMC}/search", {"query": query, "format": "json", "resultType": "lite"}
    )
    rows = ((data or {}).get("resultList") or {}).get("result") or []
    row = next((r for r in rows if r.get("pmcid")), rows[0] if rows else {})
    return row.get("pmcid"), row.get("doi")


async def _pdf_candidates(
    pmid: str | None, doi: str | None, email: str, http: httpx.AsyncClient
) -> list[tuple[str, str]]:
    """[(PDF url, version)]: Unpaywall then OpenAlex, final versions first."""
    found: list[tuple[str, str]] = []
    if doi:
        up = await _get_json(http, f"{UNPAYWALL}/{quote(doi, safe='/')}", {"email": email})
        for loc in (up or {}).get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                found.append((loc["url_for_pdf"], _VERSIONS.get(loc.get("version"), "")))
    ref = f"doi:{quote(doi, safe='/')}" if doi else (f"pmid:{pmid}" if pmid else None)
    if ref:
        oa = await _get_json(
            http, f"{OPENALEX}/{ref}", {"mailto": email, "select": "locations,best_oa_location"}
        )
        for loc in (oa or {}).get("locations") or []:
            if loc.get("pdf_url"):
                found.append((loc["pdf_url"], _VERSIONS.get(loc.get("version"), "")))
    seen: set[str] = set()
    unique = [(u, v) for u, v in found if not (u in seen or seen.add(u))]
    rank = {"published": 0, "accepted": 1, "": 2, "preprint": 3}
    return sorted(unique, key=lambda uv: rank[uv[1]])


async def _download_pdf(url: str, http: httpx.AsyncClient) -> bytes | None:
    """Open PDF; redirects are followed by hand, each hop must stay public
    (a URL provided by Unpaywall/OpenAlex must not lead to the internal network)."""
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                await asyncio.to_thread(check_public_url, url)
            except ValueError:
                return None
            async with http.stream("GET", url, headers=_UA) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = str(resp.url.join(resp.headers["location"]))
                    continue
                if resp.status_code != 200:
                    return None
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        return None
                    chunks.append(chunk)
            data = b"".join(chunks)
            return data if data.startswith(b"%PDF") else None
        return None
    except httpx.HTTPError:
        return None


async def store_pdf(session: AsyncSession, article_id, data: bytes) -> None:
    """Keeps the article's PDF (single one; replaces the old one). No commit."""
    await session.execute(delete(ArticleFile).where(ArticleFile.article_id == article_id))
    session.add(ArticleFile(article_id=article_id, data=data))


def pmc_not_archived(sections: list[dict]) -> bool:
    """The "body" returned by PMC is just the notice of a non-archivable preprint."""
    return not long_enough(sections) and any(
        "do not permit archiving in pmc" in (s.get("text") or "").lower() for s in sections
    )


def long_enough(sections: list[dict]) -> bool:
    return sum(len(s["text"]) for s in sections) >= MIN_CHARS


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "not", "into", "using",
    "between", "during", "after", "before", "among", "its", "via", "les", "des", "une",
    "dans", "pour", "sur", "par", "avec", "der", "die", "das", "und", "von", "mit", "los",
    "las", "del", "con", "por", "una",
}  # fmt: skip
HEAD_CHARS = 6000  # about the first two pages: title, authors, abstract
TITLE_COVERAGE = 0.8


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", plain)


def doi_in(text: str, doi: str | None) -> bool:
    """Does this DOI appear EXACTLY in the text (not as a prefix of another DOI)? Tolerates a
    line break between two characters of the DOI."""
    if not doi:
        return False
    pattern = r"\s?".join(re.escape(c) for c in doi.strip().lower())
    return re.search(pattern + r"(?![0-9a-z_\-]|\.[0-9a-z])", text.lower()) is not None


def pdf_matches_article(title: str | None, doi: str | None, sections: list[dict]) -> bool | None:
    """Is the extracted text really that of this article? True / False / None (unverifiable).

    The significant words of the title must appear (≥ 80%) at the top of the document;
    failing that, the article's DOI at the top of the document suffices (symbol titles,
    Greek, badly extracted…).
    """
    head = " ".join(s["text"] for s in sections)[:HEAD_CHARS]
    wanted = {w for w in _words(title or "") if len(w) >= 3 and w not in _STOPWORDS}
    if len(wanted) < 3:
        return None  # title missing or too short to decide
    if len(wanted & set(_words(head))) / len(wanted) >= TITLE_COVERAGE:
        return True
    return doi_in(head, doi)


def match_scores(sections: list[dict], candidates: list[tuple]) -> list[tuple[float, object]]:
    """Candidate articles (id, title, doi) ranked by similarity to this PDF, best first.

    2.0 = the article's DOI is at the top of the document; otherwise, fraction of
    title words found."""
    head = " ".join(s["text"] for s in sections)[:HEAD_CHARS]
    words = set(_words(head))
    scored = []
    for art_id, title, doi in candidates:
        if doi_in(head, doi):
            scored.append((2.0, art_id))  # the DOI beats a simple title
            continue
        wanted = {w for w in _words(title or "") if len(w) >= 3 and w not in _STOPWORDS}
        if len(wanted) >= 3:
            scored.append((len(wanted & words) / len(wanted), art_id))
    return sorted(scored, key=lambda t: -t[0])


async def _apply_fulltext(
    session: AsyncSession,
    art,
    sections: list[dict],
    source: str | None,
    url: str | None,
    pdf: bytes | None,
) -> None:
    """Replaces the source atomically (text, PDF, methods); the commit stays with the caller."""
    # Batch processing intercepts an error and commits the rest: this savepoint prevents keeping a
    # half-replaced source (text from one source, PDF or methods from another).
    async with session.begin_nested():
        if pdf is not None:
            await store_pdf(session, art.id, pdf)
        else:  # JATS: the old PDF no longer matches the adopted text
            await session.execute(delete(ArticleFile).where(ArticleFile.article_id == art.id))
        art.sections, art.fulltext_source, art.fulltext_url = sections, source, url
        art.fulltext_note = None
        # No methods inherited from the old source: this text's, or none.
        art.methods_text = " ".join(s["text"] for s in sections if s["kind"] == "methods") or None
        await session.flush()


async def apply_uploaded_pdf(session, art, sections: list[dict], data: bytes) -> None:
    """Adopts this PDF as the article's full text (manual upload). No commit."""
    await _apply_fulltext(session, art, sections, "pdf:upload", None, data)


async def find_open_fulltext(
    pmid: str | None,
    pmcid: str | None,
    doi: str | None,
    email: str,
    http: httpx.AsyncClient,
    title: str | None = None,
) -> tuple[list[dict], str | None, str | None, bytes | None]:
    """Full text via open sources → (sections, source, url, pdf) or 4 × empty."""
    epmc_pmcid, epmc_doi = await _epmc_ids(pmid, http, doi) if not (pmcid and doi) else (None, None)
    pmcid, doi = pmcid or epmc_pmcid, doi or epmc_doi
    if pmcid:
        sections = await _epmc_xml(pmcid, http)
        if long_enough(sections):
            return sections, "europepmc", f"https://europepmc.org/article/PMC/{pmcid}", None
    for url, version in (await _pdf_candidates(pmid, doi, email, http))[:MAX_PDF_TRIES]:
        data = await _download_pdf(url, http)
        if data is None:
            continue
        try:
            sections = pdf_to_sections(data)
        except Exception:  # noqa: BLE001 - unreadable/encrypted PDF: next candidate
            logger.info("pdf unreadable for pmid %s", pmid)
            continue
        if not long_enough(sections):
            continue
        if pdf_matches_article(title, doi, sections) is False:
            logger.info("pdf from %s is not pmid %s: skipped", urlsplit(url).hostname, pmid)
            continue  # PDF from another article (wrong resource, cover page…)
        return sections, (f"pdf:{version}" if version else "pdf"), url, data
    return [], None, None, None


async def refresh_article_metadata(session: AsyncSession, art, http: httpx.AsyncClient) -> None:
    """Re-reads the PubMed record (PMCID/DOI/title/abstract/biblio may have
    changed: published preprint)."""
    from app.pubmed import fetch_metadata
    from app.settings.service import get_setting
    from app.tasks import _biblio

    if not art.pmid:
        return
    metas = await fetch_metadata([art.pmid], await get_setting(session, "ncbi_api_key"), http)
    meta = next((m for m in metas if m.get("pmid") == art.pmid), None)
    if meta is None:
        return
    for field in ("pmcid", "doi", "title", "abstract"):
        if meta.get(field):
            setattr(art, field, meta[field])
    for key, value in _biblio(meta).items():
        if value is not None:
            setattr(art, key, value)


async def complete_article(
    session: AsyncSession, art, email: str, http: httpx.AsyncClient
) -> str | None:
    """Seeks this article's full text; found source, or None (nothing is modified).

    A manually uploaded text (``pdf:upload``) is never overwritten by an automatic search.
    """
    if art.fulltext_source == "pdf:upload" and long_enough(art.sections or []):
        return None
    sections, source, url, pdf = await find_open_fulltext(
        art.pmid, art.pmcid, art.doi, email, http, art.title
    )
    if not sections:
        return None
    await _apply_fulltext(session, art, sections, source, url, pdf)
    return source
