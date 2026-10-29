"""Références bibliographiques d'un corpus : RIS (Zotero, EndNote, Mendeley, Citavi…) et BibTeX.

Les auteurs sont stockés « Nom Initiales » (PubMed) → « Nom, Initiales » à l'export.
``notes`` : {article.id: texte} ajouté en note (statut IA, réponses…) pour retrouver le tri.
"""

from __future__ import annotations

import re

from app.sources import source_label, source_name

RIS_MIME = "application/x-research-info-systems"
BIBTEX_MIME = "application/x-bibtex"


def _author(name: str) -> str:
    """« van der Berg JA » → « van der Berg, JA » (derniers jetons = initiales en majuscules)."""
    head, _, tail = name.strip().rpartition(" ")
    return f"{head}, {tail}" if head and re.fullmatch(r"[A-Z]{1,4}", tail) else name.strip()


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _ymd(pub_date: str | None) -> tuple[str, str, str]:
    parts = (pub_date or "").split("-") + ["", ""]
    return parts[0], parts[1], parts[2]


def to_ris(articles, notes: dict | None = None) -> str:
    out: list[str] = []
    for a in articles:
        y, m, d = _ymd(a.pub_date)
        rec = [("TY", "JOUR"), ("TI", _clean(a.title))]
        rec += [("AU", _author(n)) for n in a.authors or []]
        if a.journal:
            rec += [("JO", a.journal), ("T2", a.journal)]
        if y:
            rec.append(("PY", y))
            rec.append(("DA", f"{y}/{m}/{d}" if m else y))
        rec.append(("AB", _clean(a.abstract)))
        rec.append(("DO", a.doi))
        rec.append(("AN", f"PMID:{a.pmid}" if a.pmid else None))
        rec.append(("DB", source_name(getattr(a, "source", None))))  # tag RIS « base de données »
        rec.append(("C2", a.pmcid))  # Zotero lit le PMCID dans C2
        url = f"https://doi.org/{a.doi}" if a.doi else None
        url = url or (f"https://pubmed.ncbi.nlm.nih.gov/{a.pmid}/" if a.pmid else None)
        rec += [("UR", url), ("L2", a.fulltext_url), ("LA", a.original_language)]
        rec += [("KW", t) for t in a.pub_types or []]
        rec.append(("N1", _clean((notes or {}).get(a.id))))
        rec.append(("ER", ""))
        out += [f"{k}  - {v}" for k, v in rec if v is not None and (v != "" or k == "ER")]
        out.append("")
    return "\r\n".join(out)


_BIB_ESC = str.maketrans({"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"})


def _bib(text: str | None) -> str:
    # antislash retiré AVANT l'échappement : un « \ » final échapperait l'accolade fermante
    return _clean(text).replace("\\", "").translate(_BIB_ESC).replace("{", "").replace("}", "")


def to_bibtex(articles, notes: dict | None = None) -> str:
    out: list[str] = []
    used: set[str] = set()
    for a in articles:
        base = f"pmid{a.pmid}" if a.pmid else re.sub(r"\W+", "", a.doi or "") or str(a.id)[:8]
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}_{n}"
        used.add(key)
        y, m, _ = _ymd(a.pub_date)
        fields = {
            "title": f"{{{_bib(a.title)}}}",  # accolades internes : casse conservée
            "author": " and ".join(_bib(_author(n_)) for n_ in a.authors or []),
            "journal": _bib(a.journal),
            "year": y,
            "month": str(int(m)) if m.isdigit() else "",
            "doi": a.doi or "",
            "pmid": a.pmid or "",
            "pmcid": a.pmcid or "",
            "source": _bib(source_label(getattr(a, "source", None), getattr(a, "native_id", None))),
            "url": f"https://doi.org/{a.doi}" if a.doi else "",
            "abstract": _bib(a.abstract),
            "language": a.original_language or "",
            "keywords": ", ".join(_bib(t) for t in a.pub_types or []),
            "note": _bib((notes or {}).get(a.id)),
        }
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v and v != "{}")
        out.append(f"@article{{{key},\n{body}\n}}\n")
    return "\n".join(out)
