"""Client HAL (adapter source n°4, spec 2026-10-30-sources-phase4b).

API Solr JSON gratuite sans clé (``api.archives-ouvertes.fr/search/``), pas de
rate limit documenté → throttle **par process** à 1 appel/s (pas de limiteur
Redis, même choix que ``app.pubmed`` / ``app.europepmc`` / ``app.openalex`` :
un worker d'ingestion dédié, un import à la fois par projet).

Plein texte : ``fileMain_s`` (URL du fichier principal, ~toujours PDF) —
téléchargé par ``app.fulltext._download_pdf`` (garde SSRF à chaque saut de
redirection, plafond 25 Mo, magie ``%PDF``) et sectionné par
``pdf_to_sections`` (aucun nouveau parseur). Fichier non-PDF (TEI/HTML) ou
téléchargement refusé → « non disponible » (→ ``fulltext_note`` côté pipeline).

``httpx.AsyncClient`` injecté (pas créé en dur) pour le mock en tests ;
``_sleep`` mocké en tests. Erreurs : ``HalError`` propre (SANS l'URL — le
message ne doit jamais fuiter dans ``job.error`` / logs).

shortcut: pas d'extracteur TEI/HTML (fichiers rares) ; à écrire si un corpus en dépend.
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

#: Champs demandés à l'API (itération + métadonnées — le reste est jeté).
_FL = (
    "docid,halId_s,title_s,abstract_s,authFullName_s,language_s,doiId_s,"
    "journalTitle_s,producedDate_tdate,publicationDateY_i,fileMain_s"
)

#: Tri « les plus récents d'abord » : date de production décroissante + clé
#: unique en tie-break (Solr l'exige pour un curseur stable).
_SORT = "producedDate_tdate desc,docid desc"

#: Intervalle minimum entre deux appels HAL (s) : pas de limite documentée,
#: politesse alignée sur les autres adapters.
_INTERVAL = 1.0

#: Retry sur erreur transitoire (503 / timeout), backoff simple (s) — pattern app.europepmc.
_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

#: Taille de page d'itération (HAL admet rows ≤ 10 000 ; 1000 vérifié).
_PAGE_SIZE = 1000

#: Taille de tranche de ``fetch_metadata`` (query ``halId_s:(… OR …)``).
_METADATA_CHUNK = 100

#: Mocké en tests (pas de vrai sleep).
_sleep = asyncio.sleep


class HalError(Exception):
    """Erreur HAL propre (message SANS l'URL) — même rôle que ``NCBIError``/``EpmcError``."""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise HalError(f"HTTP {resp.status_code}")


async def _throttle() -> None:
    await _sleep(_INTERVAL)


async def _get_with_retry(http: httpx.AsyncClient, params: dict) -> httpx.Response:
    """GET ``/search/`` avec retry sur erreur transitoire (503 / timeout), backoff simple."""
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
    """Filtre date Solr sur la date de production (l'analogue HAL de ``[dp]`` PubMed).

    Les bornes sont toujours ANDées (même sémantique que
    ``app.pubmed._term_bounded``) ; sans bornes → pas de ``fq``."""
    if bornes.date_from is None and bornes.date_to is None:
        return None
    lo = bornes.date_from or date(1800, 1, 1)
    hi = bornes.date_to or date.today() + timedelta(days=366)
    return f"producedDate_tdate:[{lo:%Y-%m-%d}T00:00:00Z TO {hi:%Y-%m-%d}T23:59:59Z]"


def _first(lst: list | None) -> str | None:
    """Première valeur non vide d'une liste Solr (``title_s``, ``abstract_s``…), sinon None."""
    return next((v for v in (lst or []) if v), None)


def _parse_doc(d: dict) -> dict:
    """Une notice Solr → dict meta du pipeline (clés de la spec 4b D3)."""
    meta = {
        "native_id": d.get("halId_s"),
        "title": _first(d.get("title_s")),
        "abstract": _first(d.get("abstract_s")),
        "language": _first(d.get("language_s")),
        "journal": d.get("journalTitle_s"),
        # Date de production (ISO, tronquée au jour) ; année seule en secours.
        "pub_date": (d.get("producedDate_tdate") or "")[:10]
        or (str(d["publicationDateY_i"]) if d.get("publicationDateY_i") else None),
        "authors": d.get("authFullName_s") or None,
        "fulltext_source": "hal",
    }
    if d.get("doiId_s"):
        meta["doi"] = d["doiId_s"]
    if d.get("fileMain_s"):
        # L'URL du fichier principal EST l'identifiant plein texte (fulltext_id
        # du pipeline) et le lien lisible (fulltext_url).
        meta["fulltext_id"] = d["fileMain_s"]
        meta["fulltext_url"] = d["fileMain_s"]
    else:
        meta["fulltext_note"] = "hal_no_file"
    return meta


async def _search(
    http: httpx.AsyncClient, query: str, bornes: DateBounds, rows: int, cursor: str | None = None
) -> dict:
    """Une requête ``/search/`` (throttle + retry) → le JSON **racine** de Solr
    (``response`` = numFound/docs, ``nextCursorMark`` au niveau racine)."""
    await _throttle()
    params: dict = {"q": query, "wt": "json", "rows": rows, "fl": _FL, "sort": _SORT}
    if fq := _fq_bounded(bornes):
        params["fq"] = fq
    if cursor is not None:
        params["cursorMark"] = cursor
    resp = await _get_with_retry(http, params)
    return resp.json()


class HalAdapter:
    """Adapter source HAL (n°4) : ``SourceAdapter`` sur l'API Solr HAL.

    ``api_key`` est ignoré (API sans clé) mais capté pour la signature de fabrique
    commune ``(client, api_key)``. Le count de ``search_volume`` est caché et
    réutilisé par ``iter_ids`` (le pipeline appelle toujours l'un avant l'autre).
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
        # Cap absent → le plafond d'import (``_esearch_max()``), comme les autres adapters.
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
        """URL du fichier principal (``fulltext_id`` de la notice) → ``(None, sections)``.

        PDF seulement (shortcut D2) : téléchargement refusé (SSRF, >25 Mo, non-PDF)
        ou texte vide → ``None`` (→ ``fulltext_note`` / complétion par les autres
        sources côté pipeline)."""
        if not id:
            return None
        data = await _download_pdf(id, self._http)
        if data is None:
            return None
        sections = pdf_to_sections(data)
        if not sections:
            return None  # PDF sans texte extractible : « non disponible »
        return None, sections
