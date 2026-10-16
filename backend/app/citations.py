"""Chaînage de citations : références et citations des articles de départ (iCite, NIH).

iCite couvre tout PubMed, gratuitement, bien plus vite que elink ou Europe PMC (mesuré sur un corpus
de 1 495 articles : mêmes rappels, 1 s contre 8-11 s). Un candidat est classé par le nombre
d'articles de départ auxquels il est lié : lié à plusieurs graines, il est très probablement du
même sujet.
"""

from __future__ import annotations

import httpx

from app.fulltext import _get_json

ICITE = "https://icite.od.nih.gov/api/pubs"
_BATCH = 100


async def fetch_links(
    seeds: list[str], http: httpx.AsyncClient, direction: str = "both", progress=None
) -> dict[str, set[str]]:
    """{pmid candidat: ensemble des graines liées} ; ``direction`` : backward | forward | both."""
    links: dict[str, set[str]] = {}
    for i in range(0, len(seeds), _BATCH):
        # format par défaut d'iCite (cited_by / references) ; ``legacy=false`` renomme les champs
        data = await _get_json(http, ICITE, {"pmids": ",".join(seeds[i : i + _BATCH])})
        for pub in (data or {}).get("data") or []:
            seed = str(pub.get("pmid"))
            cited: list = []
            if direction in ("backward", "both"):
                cited += pub.get("references") or []
            if direction in ("forward", "both"):
                cited += pub.get("cited_by") or []
            for pmid in cited:
                links.setdefault(str(pmid), set()).add(seed)
        if progress:
            await progress(min(i + _BATCH, len(seeds)), len(seeds))
    seed_set = set(seeds)
    return {p: s for p, s in links.items() if p not in seed_set}


def rank(links: dict[str, set[str]], known: set[str], min_overlap: int, limit: int) -> list[tuple]:
    """[(pmid, nb de graines liées)] hors corpus, les plus liés d'abord, au plus ``limit``."""
    ranked = sorted(
        ((p, len(s)) for p, s in links.items() if p not in known and len(s) >= min_overlap),
        key=lambda ps: (-ps[1], ps[0]),
    )
    return ranked[:limit]


def overlap_histogram(links: dict[str, set[str]], known: set[str]) -> dict[str, int]:
    """Candidats hors corpus ayant au moins 1, 2, 3, 5 graines liées (pour choisir le seuil)."""
    sizes = [len(s) for p, s in links.items() if p not in known]
    return {str(k): sum(1 for n in sizes if n >= k) for k in (1, 2, 3, 5)}
