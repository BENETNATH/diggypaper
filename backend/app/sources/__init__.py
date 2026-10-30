"""Contrat ``SourceAdapter`` (Phase 1, spec 2026-10-28) — contrat seul, pas d'implémentation.

``Protocol`` structurelle (typing) : toute classe exposant les 4 opérations
satisfait le contrat (``app.pubmed.PubMedAdapter``, les fakes de tests) — pas
d'héritage forcé, pas de logique commune (les 4 opérations sont des façades sur
des implémentations source-spécifiques). Le module est isolé : importable et
testable seul sans tirer ``app.pubmed`` (httpx, settings) dans l'import.

``DateBounds`` porte explicitement les bornes date utilisateur (aujourd'hui le
pipeline pré-borne la query via ``app.pubmed._term_bounded`` ; l'adapter borne
lui-même à l'intérieur de l'opération, pas de double-borne). ``None`` ≡
``DateBounds()`` ≡ bornes ouvertes.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DateBounds:
    """Bornes date utilisateur (publication), toutes deux optionnelles."""

    date_from: date | None = None
    date_to: date | None = None


class SourceAdapter(Protocol):
    """Contrat pour parler à une source (le pipeline d'import l'appelle ainsi).

    - ``search_volume(query, bornes)`` : le ``query`` est le terme **non borné** ;
      l'adapter borne (PubMed : ``_term_bounded``) avant l'appel source.
    - ``iter_ids(query, bornes, limit)`` : **génère** les ``native_id`` (PubMed :
      PMIDs), les plus récents d'abord ; ``limit`` est le cap global
      (``None`` → le cap source, défaut d'import).
    - ``fetch_metadata(ids)`` : liste d'articles natifs — au minimum
      ``native_id``, ``title``, ``abstract``, ``doi``, ``pub_date``, ``authors``
      + l'identifiant secondaire plein texte (``pmcid`` pour PubMed).
      ``ids == []`` → ``[]``.
    - ``fetch_fulltext(id)`` : ``(methods, sections)`` ; ``None`` = non
      disponible (→ ``fulltext_note`` côté pipeline). Pour PubMed, l'``id`` est
      le PMCID (identifiant secondaire de la notice).
    """

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int: ...

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]: ...

    async def fetch_metadata(self, ids: list[str]) -> list[dict]: ...

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None: ...


#: Nom lisible de chaque source (exports, UI) ; une source inconnue garde son code brut.
SOURCE_NAMES = {
    "pubmed": "PubMed",
    "europepmc": "Europe PMC",
    "openalex": "OpenAlex",
    "hal": "HAL",
    "crossref": "Crossref",
    "file": "Fichier importé",
}
#: Nature de l'identifiant natif, quand elle a un nom usuel.
_ID_KINDS = {"pubmed": "PMID", "crossref": "DOI"}


def source_name(code: str | None) -> str | None:
    """« pubmed » → « PubMed » ; code inconnu → le code ; ``None`` → ``None``."""
    return SOURCE_NAMES.get(code, code) if code else None


def source_label(code: str | None, native_id: str | None) -> str | None:
    """« PubMed PMID 123 », « Crossref DOI 10.1/x », « Fichier importé » ; ``None`` sans source."""
    name = source_name(code)
    if not name or not native_id:
        return name
    kind = _ID_KINDS.get(code)
    return f"{name} {kind} {native_id}" if kind else f"{name} {native_id}"
