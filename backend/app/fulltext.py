"""Texte intégral hors PMC : sources ouvertes légales (open access, préprints).

Ordre : full text JATS Europe PMC (par PMCID, trouvé via le PMID si besoin) → PDF ouverts
listés par Unpaywall puis OpenAlex (versions éditeur, manuscrit accepté, préprint
bioRxiv/medRxiv/arXiv…). Les articles réellement payants sans copie ouverte restent
sans texte intégral (abstract seul) ; aucune source pirate.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from urllib.parse import quote

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

MIN_CHARS = 1500  # en dessous : page d'accueil / paywall, pas un article
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
    """Texte d'un PDF → [{title, kind, text}] découpé aux titres usuels (IMRaD)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    lines: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        # NUL interdit dans JSONB Postgres ("unsupported Unicode escape sequence")
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
            return sections  # références / remerciements : fin du corps
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
    except Exception:  # noqa: BLE001 - réseau ou XML mal formé : source suivante
        return []


async def _epmc_ids(pmid: str | None, http: httpx.AsyncClient) -> tuple[str | None, str | None]:
    """(pmcid, doi) connus d'Europe PMC pour ce PMID."""
    if not pmid:
        return None, None
    data = await _get_json(
        http,
        f"{EPMC}/search",
        {"query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json", "resultType": "lite"},
    )
    rows = ((data or {}).get("resultList") or {}).get("result") or []
    return (rows[0].get("pmcid"), rows[0].get("doi")) if rows else (None, None)


async def _pdf_candidates(
    pmid: str | None, doi: str | None, email: str, http: httpx.AsyncClient
) -> list[tuple[str, str]]:
    """[(url PDF, version)] : Unpaywall puis OpenAlex, versions finales d'abord."""
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
    """PDF ouvert ; les redirections sont suivies à la main, chaque saut doit rester public
    (une URL fournie par Unpaywall/OpenAlex ne doit pas mener au réseau interne)."""
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
    """Conserve le PDF de l'article (un seul ; remplace l'ancien). Pas de commit."""
    await session.execute(delete(ArticleFile).where(ArticleFile.article_id == article_id))
    session.add(ArticleFile(article_id=article_id, data=data))


def long_enough(sections: list[dict]) -> bool:
    return sum(len(s["text"]) for s in sections) >= MIN_CHARS


async def find_open_fulltext(
    pmid: str | None,
    pmcid: str | None,
    doi: str | None,
    email: str,
    http: httpx.AsyncClient,
) -> tuple[list[dict], str | None, str | None, bytes | None]:
    """Texte intégral en sources ouvertes → (sections, source, url, pdf) ou 4 × vide."""
    epmc_pmcid, epmc_doi = await _epmc_ids(pmid, http) if not (pmcid and doi) else (None, None)
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
        except Exception:  # noqa: BLE001 - PDF illisible/chiffré : candidat suivant
            logger.info("pdf unreadable for pmid %s", pmid)
            continue
        if long_enough(sections):
            return sections, (f"pdf:{version}" if version else "pdf"), url, data
    return [], None, None, None
