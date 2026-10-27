"""Client NCBI E-utilities (PubMed/PMC) — Module 2.

``httpx.AsyncClient`` est injecté (pas créé en dur) pour permettre le mock en
tests. ``_sleep`` est mocké en tests (pas de vrai sleep).

Rate limiting NCBI (M5) : 3 req/s sans clé API, 10 req/s avec — le throttle est
**par process** (``asyncio.sleep`` entre appels, pas de limiteur Redis : le
volume d'ingestion est faible, un limiteur partagé serait de la sur-ingénierie).
Conséquence en production multi-workers : chaque worker Celery a son propre
compteur → **contrainte de déploiement obligatoire (M-3) : un seul worker dédié
à l'ingestion** (``celery -A app.celery_app worker -c 1``) pour rester sous le
plafond NCBI. Les rate limiting applicatifs (M-1/M-2) + la garde « un import par
projet » (M-2) mitigent le DoS applicatif ; pas de limiteur Redis.

Erreurs (C-1) : les erreurs HTTP NCBI (>= 400) et les timeouts lèvent
``NCBIError`` (message propre, SANS l'URL ni la clé API) — pas
``httpx.HTTPStatusError`` (dont le str() contient l'URL complète avec
``api_key=…``). Ça évite la fuite de la clé NCBI via ``job.error`` / logs.

``oa.fcgi`` (``fetch_methods``) n'est pas un E-utility : le throttle 1/3 s est
conservé par prudence (m12).

Retry (m5) : 2 retries avec backoff simple sur erreur transitoire (503 / timeout)
pour esearch/efetch. Pas de retry sur erreur de parsing (le parsing est fait par
l'appelant, après la réponse).

Limite de taille (I5) : les payloads XML NCBI > 10 Mo sont refusés (défense en
profondeur — l'entrée est NCBI via HTTPS, pas contrôlée par l'utilisateur ; la
limite de taille suffit, pas de defusedxml).
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

#: Intervalle minimum entre deux appels NCBI (s) : 3 req/s sans clé, 10 req/s avec.
_INTERVAL_NO_KEY = 1 / 3
_INTERVAL_WITH_KEY = 0.1


#: Plafond d'articles par import (IMPORT_MAX_ARTICLES, défaut 5000 ; 0 = illimité).
UNLIMITED = 10**9


def _esearch_max() -> int:
    from app.core.config import get_settings

    n = get_settings().import_max_articles
    return n if n > 0 else UNLIMITED


#: Une requête esearch ne renvoie jamais plus de 10 000 identifiants PubMed (limite NCBI, même
#: avec ``retstart``) : au-delà, on découpe par date de publication (cf. ``fetch_all_pmids``).
PUBMED_WINDOW = 9999


#: m5 : retry sur erreur transitoire (503 / timeout), backoff simple (s).
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: I5 : limite de taille des payloads XML NCBI (défense en profondeur) : 10 Mo.
_MAX_XML_BYTES = 10 * 1024 * 1024

#: Mocké en tests (pas de vrai sleep).
_sleep = asyncio.sleep

#: Titres de section reconnus comme "Methods" (insensible à la casse).
_METHODS_TITLES = {"methods", "materials and methods", "méthodes"}


class NCBIError(Exception):
    """C-1 : erreur NCBI propre (message SANS l'URL ni la clé API).

    Remplace ``httpx.HTTPStatusError`` (dont le str() contient l'URL complète
    avec ``api_key=…``) pour éviter la fuite de la clé NCBI via ``job.error``
    et les logs serveur.
    """


def _raise_for_status(resp: httpx.Response) -> None:
    """C-1 : lève NCBIError (SANS l'URL) au lieu de httpx.HTTPStatusError."""
    if resp.status_code >= 400:
        raise NCBIError(f"HTTP {resp.status_code}")


async def _throttle(api_key: str | None) -> None:
    """Espace les appels NCBI (3 req/s sans clé, 10 req/s avec clé)."""
    await _sleep(_INTERVAL_WITH_KEY if api_key else _INTERVAL_NO_KEY)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict, *, retries: int = _RETRIES
) -> httpx.Response:
    """GET NCBI avec retry sur erreur transitoire (m5) : 503 / timeout.

    ``retries`` retries avec backoff simple (``_sleep``, mocké en tests).
    Pas de retry sur erreur de parsing (le parsing est fait par l'appelant).
    C-1 : timeout persistant → NCBIError('timeout') ; erreur HTTP persistante →
    NCBIError('HTTP <code>') (SANS l'URL ni la clé).
    """
    for attempt in range(retries + 1):
        try:
            resp = await http.get(url, params=params)
        except httpx.TimeoutException:
            if attempt >= retries:
                raise NCBIError("timeout") from None
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        if resp.status_code == 503 and attempt < retries:
            await _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        _raise_for_status(resp)
        return resp


def _check_xml_size(resp: httpx.Response) -> None:
    """I5 : refuse les payloads XML NCBI > 10 Mo (défense en profondeur)."""
    if len(resp.content) > _MAX_XML_BYTES:
        raise ValueError(f"NCBI XML payload too large: {len(resp.content)} bytes")


def _esearch_params(query: str, retmax: int, api_key: str | None) -> dict:
    params = {"db": "pubmed", "term": query, "retmax": retmax, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    return params


async def search_volume(query: str, api_key: str | None, http: httpx.AsyncClient) -> int:
    """Volume de résultats PubMed (esearch ``retmax=0``) → count (int)."""
    await _throttle(api_key)
    resp = await _get_with_retry(
        http, f"{EUTILS_BASE}/esearch.fcgi", _esearch_params(query, 0, api_key)
    )
    return int(resp.json()["esearchresult"]["count"])


async def explain_query(query: str, api_key: str | None, http: httpx.AsyncClient) -> dict:
    """Comment PubMed comprend la requête : {count, translation, not_found}.

    ``translation`` = la requête réécrite par PubMed (termes MeSH, champs) ; ``not_found`` = termes
    ou champs qu'il ne connaît pas (MeSH inventé, faute de frappe) : signal d'une requête fausse.
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
    """PMIDs d'une requête (esearch) : au plus ``retmax`` (défaut : le plafond d'import) et jamais
    plus que la limite de NCBI (``PUBMED_WINDOW`` identifiants par requête)."""
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
) -> list[str]:
    """Tous les PMIDs d'une requête, même au-delà de 10 000 résultats.

    NCBI ne livre que 9 999 identifiants par requête : une requête plus grosse est découpée en
    tranches de dates de publication (bissection tant qu'une tranche dépasse la limite), les plus
    récentes d'abord ; les doublons (dates imprimée / électronique) sont retirés. ``limit`` borne
    le total (les plus récents sont gardés).

    Ordre : « les plus récents d'abord » tient au niveau des tranches (la bissection visite les
    tranches les plus récentes en premier, NCBI ne triant pas par date au sein d'une tranche) ;
    les identifiants d'une tranche sont insérés en inversant l'ordre renvoyé par esearch, qui
    est croissant par date sur les fenêtres étroites.
    """
    limit = limit or _esearch_max()
    if count <= PUBMED_WINDOW:
        return list(reversed(await fetch_pmids(query, api_key, http, min(count, limit))))
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

    await walk(date(1800, 1, 1), date.today() + timedelta(days=366))
    if len(found) < count:
        logger.warning(
            "PubMed: %d identifiants sur %d (articles sans date de publication)", len(found), count
        )
    return list(found)[:limit]


async def fetch_metadata(
    pmids: list[str], api_key: str | None, http: httpx.AsyncClient
) -> list[dict]:
    """Métadonnées PubMed (efetch XML) : pmid, pmcid?, doi?, title, abstract, language."""
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
    """Section Methods du full-text PMC (efetch ``db=pmc``, JATS) → texte.

    ``None`` si pas de pmcid ou pas de section Methods.
    L'efetch ``db=pmc`` est un E-utility → throttle NCBI (3 req/s sans clé).
    (L'ancien ``oa.fcgi`` est obsolète : 404 systématique, constaté 2026-10-12.)
    """
    xml = await _fetch_pmc_xml(pmcid, http)
    return _extract_methods(xml) if xml else None


async def fetch_fulltext(
    pmcid: str | None, http: httpx.AsyncClient
) -> tuple[str | None, list[dict]]:
    """Full text PMC en un seul appel : (texte Methods, sections [{title, kind, text}])."""
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


# --- Parsing XML (xml.etree.ElementTree, sans namespace) ---


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
    """Date de publication ISO partielle : date électronique, sinon date du fascicule."""
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
    """Un dict par ``PubmedArticle`` : pmid, pmcid?, doi?, title, abstract, language."""
    root = ET.fromstring(xml_text)
    articles: list[dict] = []
    for article in root.iter("PubmedArticle"):
        articles.append(
            {
                # PMID est enfant direct de MedlineCitation (pas dans Article).
                "pmid": _text(article, "MedlineCitation/PMID"),
                # ArticleIdList est dans PubmedData (sibling de MedlineCitation).
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
    """Texte de la première section JATS titrée Methods / Materials and Methods / Méthodes.

    Le full-text PMC est en JATS (tags minuscules : ``sec``, ``title``, ``p``) —
    pas le format MEDLINE (``Sec``/``Title``). Les sous-sections imbriquées sont
    incluses (``sec.iter('p')`` parcourt l'arbre entier).
    """
    root = ET.fromstring(xml_text)
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
    """Texte d'un élément JATS sans le bruit des formules : le LaTeX (``tex-math``, préambule
    ``\\documentclass…`` compris) est ignoré ; une formule hors texte devient « [formula] » ; le
    MathML en ligne (p < 0,05, x²…) est gardé."""
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
    """Sections de premier niveau du ``body`` JATS : [{title, kind, text}]."""
    from app.article_text import classify_title

    root = ET.fromstring(xml_text)
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
