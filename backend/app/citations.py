"""Citation chaining: references and citations of the seed articles (iCite, NIH).

iCite covers all of PubMed, free, far faster than elink or Europe PMC (measured
on a corpus of 1,495 articles: same recall, 1 s vs 8-11 s). A candidate is
ranked by the number of seed articles it is linked to: linked to several seeds,
it is very likely on the same topic.
"""

from __future__ import annotations

import httpx

from app.fulltext import _get_json

ICITE = "https://icite.od.nih.gov/api/pubs"
_BATCH = 100


async def fetch_links(
    seeds: list[str], http: httpx.AsyncClient, direction: str = "both", progress=None
) -> dict[str, set[str]]:
    """{candidate pmid: set of linked seeds}; ``direction``: backward | forward | both."""
    links: dict[str, set[str]] = {}
    for i in range(0, len(seeds), _BATCH):
        # iCite default format (cited_by / references); ``legacy=false`` renames the fields
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
    """[(pmid, number of linked seeds)] outside the corpus, most linked first, at most ``limit``."""
    ranked = sorted(
        ((p, len(s)) for p, s in links.items() if p not in known and len(s) >= min_overlap),
        key=lambda ps: (-ps[1], ps[0]),
    )
    return ranked[:limit]


def overlap_histogram(links: dict[str, set[str]], known: set[str]) -> dict[str, int]:
    """Candidates outside the corpus with at least 1, 2, 3, 5 linked seeds (to pick
    the threshold)."""
    sizes = [len(s) for p, s in links.items() if p not in known]
    return {str(k): sum(1 for n in sizes if n >= k) for k in (1, 2, 3, 5)}
