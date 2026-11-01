"""``SourceAdapter`` contract (Phase 1, spec 2026-10-28) — contract only, no implementation.

Structural ``Protocol`` (typing): any class exposing the 4 operations
satisfies the contract (``app.pubmed.PubMedAdapter``, the test fakes) — no
forced inheritance, no shared logic (the 4 operations are facades over
source-specific implementations). The module is isolated: importable and
testable on its own without dragging ``app.pubmed`` (httpx, settings) into the import.

``DateBounds`` explicitly carries the user date bounds (today the pipeline
pre-bounds the query via ``app.pubmed._term_bounded``; the adapter bounds
itself inside the operation, no double-bounding). ``None`` ≡
``DateBounds()`` ≡ open bounds.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DateBounds:
    """User date bounds (publication), both optional."""

    date_from: date | None = None
    date_to: date | None = None


class SourceAdapter(Protocol):
    """Contract to talk to a source (the import pipeline calls it this way).

    - ``search_volume(query, bounds)``: the ``query`` is the **unbounded** term;
    the adapter bounds it (PubMed: ``_term_bounded``) before the source call.
    - ``iter_ids(query, bounds, limit)``: **yields** the ``native_id``s (PubMed:
    PMIDs), most recent first; ``limit`` is the global cap
    (``None`` → the source cap, import default).
    - ``fetch_metadata(ids)``: list of native articles — at minimum
    ``native_id``, ``title``, ``abstract``, ``doi``, ``pub_date``, ``authors``
    + the secondary full-text identifier (``pmcid`` for PubMed).
    ``ids == []`` → ``[]``.
    - ``fetch_fulltext(id)``: ``(methods, sections)``; ``None`` = not
    available (→ ``fulltext_note`` on the pipeline side). For PubMed, the ``id``
    is the PMCID (secondary identifier of the record).
    """

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int: ...

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]: ...

    async def fetch_metadata(self, ids: list[str]) -> list[dict]: ...

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None: ...


#: Readable name of each source (exports, UI); an unknown source keeps its raw code.
SOURCE_NAMES = {
    "pubmed": "PubMed",
    "europepmc": "Europe PMC",
    "openalex": "OpenAlex",
    "hal": "HAL",
    "crossref": "Crossref",
    "file": "Imported file",
}
#: Nature of the native identifier, when it has a common name.
_ID_KINDS = {"pubmed": "PMID", "crossref": "DOI"}


def source_name(code: str | None) -> str | None:
    """ "pubmed" → "PubMed"; unknown code → the code; ``None`` → ``None``."""
    return SOURCE_NAMES.get(code, code) if code else None


def source_label(code: str | None, native_id: str | None) -> str | None:
    """ "PubMed PMID 123", "Crossref DOI 10.1/x", "Imported file"; ``None`` without a source."""
    name = source_name(code)
    if not name or not native_id:
        return name
    kind = _ID_KINDS.get(code)
    return f"{name} {kind} {native_id}" if kind else f"{name} {native_id}"
