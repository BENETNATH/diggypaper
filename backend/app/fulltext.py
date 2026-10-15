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


def pmc_not_archived(sections: list[dict]) -> bool:
    """Le « corps » renvoyé par PMC n'est que l'avis d'un préprint non archivable."""
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
HEAD_CHARS = 6000  # ≈ les deux premières pages : titre, auteurs, résumé
TITLE_COVERAGE = 0.8


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", plain)


def pdf_matches_article(title: str | None, doi: str | None, sections: list[dict]) -> bool | None:
    """Le texte extrait est-il bien celui de cet article ? True / False / None (invérifiable).

    Les mots significatifs du titre doivent figurer (≥ 80 %) en tête du document ; à défaut, le
    DOI de l'article en tête de document suffit (titre à symboles, grec, mal extrait…).
    """
    head = " ".join(s["text"] for s in sections)[:HEAD_CHARS]
    wanted = {w for w in _words(title or "") if len(w) >= 3 and w not in _STOPWORDS}
    if len(wanted) < 3:
        return None  # titre absent ou trop court pour trancher
    if len(wanted & set(_words(head))) / len(wanted) >= TITLE_COVERAGE:
        return True
    squashed = "".join(_words(head))
    if doi and "".join(_words(doi)) in squashed:
        return True
    return False


def match_scores(sections: list[dict], candidates: list[tuple]) -> list[tuple[float, object]]:
    """Articles candidats (id, titre, doi) classés par ressemblance avec ce PDF, meilleurs d'abord.

    2.0 = le DOI de l'article est en tête du document ; sinon, part des mots du titre retrouvés."""
    head = " ".join(s["text"] for s in sections)[:HEAD_CHARS]
    words, squashed = set(_words(head)), "".join(_words(head))
    scored = []
    for art_id, title, doi in candidates:
        if doi and "".join(_words(doi)) in squashed:
            scored.append((2.0, art_id))  # le DOI prime sur un simple titre
            continue
        wanted = {w for w in _words(title or "") if len(w) >= 3 and w not in _STOPWORDS}
        if len(wanted) >= 3:
            scored.append((len(wanted & words) / len(wanted), art_id))
    return sorted(scored, key=lambda t: -t[0])


async def apply_uploaded_pdf(session, art, sections: list[dict], data: bytes) -> None:
    """Adopte ce PDF comme texte intégral de l'article (dépôt manuel). Pas de commit."""
    art.sections, art.fulltext_source = sections, "pdf:upload"
    art.fulltext_note, art.fulltext_url = None, None
    await store_pdf(session, art.id, data)
    art.methods_text = " ".join(s["text"] for s in sections if s["kind"] == "methods") or None


async def find_open_fulltext(
    pmid: str | None,
    pmcid: str | None,
    doi: str | None,
    email: str,
    http: httpx.AsyncClient,
    title: str | None = None,
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
        if not long_enough(sections):
            continue
        if pdf_matches_article(title, doi, sections) is False:
            logger.info("pdf from %s is not pmid %s: skipped", urlsplit(url).hostname, pmid)
            continue  # PDF d'un autre article (mauvaise ressource, page de couverture…)
        return sections, (f"pdf:{version}" if version else "pdf"), url, data
    return [], None, None, None


async def refresh_article_metadata(session: AsyncSession, art, http: httpx.AsyncClient) -> None:
    """Relit la notice PubMed (PMCID/DOI/titre/résumé/biblio ayant pu changer : préprint publié)."""
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
    """Cherche le texte intégral de cet article ; source trouvée, ou None (rien n'est modifié).

    Un texte déposé à la main (``pdf:upload``) n'est jamais écrasé par une recherche automatique.
    """
    if art.fulltext_source == "pdf:upload" and long_enough(art.sections or []):
        return None
    sections, source, url, pdf = await find_open_fulltext(
        art.pmid, art.pmcid, art.doi, email, http, art.title
    )
    if not sections:
        return None
    art.sections, art.fulltext_source, art.fulltext_url = sections, source, url
    art.fulltext_note = None
    if pdf:
        await store_pdf(session, art.id, pdf)
    art.methods_text = (
        " ".join(s["text"] for s in sections if s["kind"] == "methods") or art.methods_text
    )
    return source
