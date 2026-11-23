"""Bibliographic references of a corpus: RIS (Zotero, EndNote, Mendeley, Citavi…) and BibTeX.

Authors are stored "Name Initials" (PubMed) → "Name, Initials" at export.
``notes``: {article.id: text} added as a note (AI status, answers…) to recover the triage.
"""

from __future__ import annotations

import re

from app.sources import source_label, source_name

RIS_MIME = "application/x-research-info-systems"
BIBTEX_MIME = "application/x-bibtex"

_PDF_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')


def _title_part(title: str | None) -> str:
    """Titre borné à 100 caractères (frontière de mot), ponctuation de fin retirée,
    « … » si tronqué."""
    t = re.sub(r"\s+", " ", (title or "").strip()).rstrip(".,;:!?").strip()
    if len(t) <= 100:
        return t
    cut = t[:100]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(".,;:!?").strip() + "…"


def pdf_filename(article, taken: set[str] | None = None) -> str:
    """Nom lisible du PDF conservé (« hussain et al. 2026 acute-nicotine….pdf »),
    partagé par l'export ZIP et le RO-Crate.

    ``taken`` : noms déjà posés dans le même lot → suffixes « (2) », « (3) »…
    """
    title = _title_part(article.title)
    words = re.sub(r"\s+", " ", (article.authors or [""])[0]).split()
    if not words:  # pas d'auteur : le nom démarre par le titre
        name = title
    else:
        head = words[0].lower() + (" et al." if len(article.authors) > 1 else "")
        if article.pub_year:
            head += f" {article.pub_year}"
        name = f"{head} {title}"
    name = re.sub(r"\s{2,}", " ", _PDF_BAD_CHARS.sub("", name)).strip() or "article"
    if taken is not None:
        stem, n = name, 1
        while f"{stem}.pdf" in taken:
            n += 1
            stem = f"{name} ({n})"
        name = stem
        taken.add(name + ".pdf")
    return name + ".pdf"


def _author(name: str) -> str:
    """ "van der Berg JA" → "van der Berg, JA" (last tokens = uppercase initials)."""
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
        rec.append(
            (
                "AN",
                f"PMID:{a.pmid}" if a.pmid else source_label(a.source, a.native_id),
            )
        )
        rec.append(("DB", source_name(a.source)))  # RIS "database" tag
        rec.append(("C2", a.pmcid))  # Zotero reads the PMCID from C2
        url = f"https://doi.org/{a.doi}" if a.doi else None
        url = url or (f"https://pubmed.ncbi.nlm.nih.gov/{a.pmid}/" if a.pmid else None)
        rec += [("UR", url), ("L2", a.fulltext_url), ("LA", a.original_language)]
        rec += [("KW", t) for t in a.pub_types or []]
        rec.append(("N1", _clean((notes or {}).get(a.id))))
        rec.append(("ER", ""))
        # Every value goes through _clean: a line break would forge RIS records.
        cleaned = [(k, _clean(str(v))) for k, v in rec if v is not None]
        out += [f"{k}  - {v}" for k, v in cleaned if v or k == "ER"]
        out.append("")
    return "\r\n".join(out)


_BIB_ESC = str.maketrans({"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"})


def _bib(text: str | None) -> str:
    # backslash removed BEFORE escaping: a final backslash would escape the closing brace
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
            "title": f"{{{_bib(a.title)}}}",  # inner braces: case preserved
            "author": " and ".join(_bib(_author(n_)) for n_ in a.authors or []),
            "journal": _bib(a.journal),
            "year": y,
            "month": str(int(m)) if m.isdigit() else "",
            "doi": a.doi or "",
            "pmid": a.pmid or "",
            "pmcid": a.pmcid or "",
            "source": _bib(source_label(a.source, a.native_id)),
            "url": f"https://doi.org/{a.doi}" if a.doi else "",
            "abstract": _bib(a.abstract),
            "language": a.original_language or "",
            "keywords": ", ".join(_bib(t) for t in a.pub_types or []),
            "note": _bib((notes or {}).get(a.id)),
        }
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v and v != "{}")
        out.append(f"@article{{{key},\n{body}\n}}\n")
    return "\n".join(out)
