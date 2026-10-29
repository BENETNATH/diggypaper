"""Client OpenAlex Works API (adapter source n°3, spec 2026-10-29-sources-phase4a D1).

Source **métadonnées seulement** : ``fetch_fulltext`` renvoie toujours ``None`` (→
``fulltext_note="metadata_only"`` côté pipeline, jamais de texte intégré).

Usage-based depuis fév. 2026 : sans clé, budget IP partagé gratuit ($0.10/jour,
``meta.notice`` dans chaque réponse) ; avec la config sécrète ``openalex_api_key``,
budget dédié (paramètre ``api_key``). Rate limit signalé par 429 → retry backoff +
throttle **par process** à 1 appel/s (pas de limiteur Redis, même choix que
``app.pubmed`` / ``app.europepmc`` — un worker d'ingestion dédié, un import à la fois
par projet).

``httpx.AsyncClient`` injecté (pas créé en dur) pour le mock en tests ; ``_sleep``
mocké en tests. Erreurs : ``OpenAlexError`` propre (SANS l'URL — le message ne doit
jamais fuiter dans ``job.error`` / logs).

Bornes dates : dans ``filter`` (``from_publication_date:…`` /
``to_publication_date:…``), **jamais** paramètres de requête (400 sinon) ;
``meta.count`` est porté par chaque réponse (pas d'appel count séparé). Pas de
filtre par identifiant de work (``filter=id:…`` → 400) → ``fetch_metadata`` fait un
``GET /works/{id}`` par id.
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

#: Intervalle minimum entre deux appels OpenAlex (s) : budget IP partagé sans clé.
_INTERVAL = 1.0

#: Retry sur erreur transitoire (429 / 5xx / timeout), backoff simple (s) — pattern app.europepmc.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Taille de page d'itération (OpenAlex admet per_page ≤ 200).
_PAGE_SIZE = 200

#: Mocké en tests (pas de vrai sleep).
_sleep = asyncio.sleep


class OpenAlexError(Exception):
    """Erreur OpenAlex propre (message SANS l'URL) — même rôle que ``NCBIError``/``EpmcError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise OpenAlexError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(
    http: httpx.AsyncClient, url: str, params: dict | None = None
) -> httpx.Response:
    """GET OpenAlex avec retry sur erreur transitoire (429 / 5xx / timeout), backoff simple."""
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
    """Bornes dates → terme ``filter`` (OpenAlex n'en accepte pas en paramètre de requête)."""
    terms: list[str] = []
    if bornes.date_from is not None:
        terms.append(f"from_publication_date:{bornes.date_from:%Y-%m-%d}")
    if bornes.date_to is not None:
        terms.append(f"to_publication_date:{bornes.date_to:%Y-%m-%d}")
    return ",".join(terms) or None


def _bare_id(value: str | None) -> str | None:
    """Dernier segment de chemin d'une URL d'identifiant (``ids.pmid`` / ``ids.pmcid``
    sont des URL) → valeur nue pour l'identité et le rattachement DOI."""
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1] or None


def _rebuild_abstract(inverted: dict | None) -> str | None:
    """``abstract_inverted_index`` (mot → positions 0-based) → texte (tri par position)."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for pos in idxs:
            positions.append((pos, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _parse_work(w: dict) -> dict:
    """Un record work → dict meta du pipeline (clés du contrat + ``fulltext_note``)."""
    meta: dict = {
        "native_id": (w.get("id") or "").removeprefix(_ID_PREFIX) or None,
        "title": w.get("display_name"),
        "abstract": _rebuild_abstract(w.get("abstract_inverted_index")),
        "language": w.get("language"),
        "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "pub_date": w.get("publication_date"),
        "fulltext_note": "metadata_only",  # D2 : source métadonnées seulement
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
    """Adapter source OpenAlex (n°3) : ``SourceAdapter`` sur la Works API.

    ``api_key`` (config sécrète ``openalex_api_key``) : envoyée en paramètre
    ``api_key`` si posée (budget dédié) ; sans clé, pool IP gratuit. Le count de
    ``search_volume`` est porté par chaque réponse (``meta.count``) ; ``iter_ids``
    ne refait jamais d'appel de count (pagination par page, ``sort`` plus récents
    d'abord).
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
            # page fetchée complète (page × per_page couvre le count) ou vide → fin
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
            # Pas de filtre par id de work (400) : l'objet unique, un appel par id.
            await _throttle()
            resp = await _get_with_retry(self._http, f"{OALEX_BASE}/{wid}", self._key_params())
            w = resp.json()
            if w.get("id"):
                metas.append(_parse_work(w))
        return metas

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        return None  # source métadonnées seulement (→ fulltext_note "metadata_only")

    def _key_params(self) -> dict | None:
        return {"api_key": self._api_key} if self._api_key else None
