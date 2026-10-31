"""Client Europe PMC REST (adapter source n°2, spec 2026-10-29-sources-phase3).

API gratuite sans clé, rate limit par IP faible et non documenté (503 après de
courtes rafales, observé 2026-10-29 depuis une IP datacenter) → throttle **par
process** à 1 appel/s (pas de limiteur Redis, même choix que ``app.pubmed`` :
contrainte de déploiement — un worker d'ingestion dédié, un import à la fois par
projet).

``httpx.AsyncClient`` injecté (pas créé en dur) pour le mock en tests ; ``_sleep``
mocké en tests. Erreurs : ``EpmcError`` propre (SANS l'URL — le message ne doit
jamais fuiter dans ``job.error`` / logs), retry backoff sur 503/timeout.

Plein texte : ``GET /{id}/fullTextXML`` renvoie du JATS — le parseur est réutilisé
tel quel depuis ``app.pubmed`` (``_extract_methods``/``_extract_sections``),
contrainte d'architecture (aucun nouveau parseur).
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

#: Intervalle minimum entre deux appels EPMC (s) : rate limit par IP, sans clé.
_INTERVAL = 1.0

#: Retry sur erreur transitoire (503 / timeout), backoff simple (s) — pattern app.pubmed.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Taille de page d'itération (EPMC admet pageSize ≤ 1000).
_PAGE_SIZE = 1000

#: Taille de tranche de ``fetch_metadata`` (query ``id:(… OR …)``).
_METADATA_CHUNK = 100

#: Mocké en tests (pas de vrai sleep).
_sleep = asyncio.sleep


class EpmcError(Exception):
    """Erreur EPMC propre (message SANS l'URL) — même rôle que ``NCBIError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise EpmcError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict | None = None
) -> httpx.Response:
    """GET EPMC avec retry sur erreur transitoire (503 / timeout), backoff simple."""
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
    """Requête bornée par date de première publication (``FIRST_PDATE``).

    Les bornes sont toujours ANDées, même si la query contient déjà une borne date
    (même sémantique que ``app.pubmed._term_bounded``) ; sans bornes : query brute."""
    if bornes.date_from is None and bornes.date_to is None:
        return query
    lo = bornes.date_from or date(1800, 1, 1)
    hi = bornes.date_to or date.today() + timedelta(days=366)
    return f"({query}) AND FIRST_PDATE:[{lo:%Y-%m-%d} TO {hi:%Y-%m-%d}]"


def _first(lst: list | None) -> str | None:
    """Première valeur non vide d'une liste EPMC, sinon None."""
    return next((v for v in (lst or []) if v), None)


def _parse_result(r: dict) -> dict:
    """Une notice ``resultType=core`` → dict meta du pipeline (clés de la spec D1)."""
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
    """Adapter source Europe PMC (n°2) : ``SourceAdapter`` sur le REST EPMC.

    ``api_key`` est ignoré (API sans clé) mais capté pour la signature de fabrique
    commune ``(client, api_key)``. Le count de ``search_volume`` est caché et
    réutilisé par ``iter_ids`` (le pipeline appelle toujours l'un avant l'autre).
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key  # ignoré : Europe PMC ne prend pas de clé
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
        # Cap absent → le plafond d'import (``_esearch_max()``), comme l'adapter PubMed.
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
                # 2026-10-31 : EBI renvoyait 503 uniquement quand ``sort`` était passé — repli sans
                # tri (ordre arbitraire) pour que l'import passe ; le tri revient dès que EBI est OK.
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
            # ``sort=False`` : l'ordre est sans intérêt pour un fetch par identifiants (et le 503
            # EBI de 2026-10-31 frappait toute requête portant ``sort``).
            data = await _search(self._http, query, len(chunk), None, result_type="core", sort=False)
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
            # pas de texte (record non-OA) → « non disponible » côté pipeline
            if str(exc) in ("HTTP 404", "HTTP 403"):
                return None
            raise
        _check_xml_size(resp)
        methods, sections = _extract_methods(resp.text), _extract_sections(resp.text)
        if methods is None and not sections:
            return None  # « non disponible » (pas de XML) → fulltext_note côté pipeline
        return methods, sections


async def _search(
    http: httpx.AsyncClient,
    query: str,
    page_size: int,
    cursor: str | None,
    result_type: str = "lite",
    sort: bool = True,
) -> dict:
    """Une requête ``/search`` (throttle + retry) → le JSON (hitCount, resultList…).

    ``sort=False`` pour les appels de count : le tri est inutile (le hitCount ne dépend pas de
    l'ordre) et le 2026-10-31 l'endpoint EPMC renvoyait 503 uniquement quand ``sort`` était passé.
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
