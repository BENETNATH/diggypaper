"""Import of a references list: DOI / PMID, BibTeX, RIS, CSL JSON, CSV (Zotero…).

``parse_references`` returns entries ``{doi, pmid, title, authors, journal, year, abstract,
language}`` (absent fields = None). Resolution (PubMed via Europe PMC, Crossref) is here too.
"""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

from app.fulltext import _get_json

CROSSREF = "https://api.crossref.org/works"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>\\{}]+", re.I)
_PMID_LABEL = re.compile(r"(?:pmid|pubmed(?:/|\.ncbi\.nlm\.nih\.gov/)?)\W{0,3}(\d{1,9})\b", re.I)


def clean_doi(value: str | None) -> str | None:
    """ "https://doi.org/10.1/X." → "10.1/x" (lowercase: a DOI is case-insensitive)."""
    m = _DOI.search(value or "")
    return m.group(0).rstrip(".,;:)]").lower() if m else None


def norm_title(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _clean(text: str | None) -> str | None:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    return text or None


def _author(name: str) -> str:
    """ "Dupont, Jean Paul" / "Jean Paul Dupont" → "Dupont JP" (the corpus' PubMed format)."""
    name = name.strip()
    if "," in name:
        last, _, first = name.partition(",")
    else:
        first, _, last = name.rpartition(" ")
    initials = "".join(p[0] for p in re.split(r"[\s.-]+", first.strip()) if p).upper()
    return f"{last.strip()} {initials}".strip()


def _year(value: str | None) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", value or "")
    return int(m.group(1)) if m else None


def _entry(**kw) -> dict:
    e = {
        k: kw.get(k)
        for k in ("doi", "pmid", "title", "authors", "journal", "year", "abstract", "language")
    }
    e["doi"] = clean_doi(e["doi"])
    e["title"] = _clean(e["title"])
    e["abstract"] = _clean(e["abstract"])
    pm = re.search(r"\d{1,9}", str(e["pmid"] or ""))
    e["pmid"] = pm.group(0) if pm else None
    return e


# --- formats -----------------------------------------------------------------


def parse_plain(text: str) -> list[dict]:
    """One reference per line: DOI (or doi.org link) or PMID (bare number or "PMID: n")."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        doi = clean_doi(line)
        m = re.fullmatch(r"\d{1,9}", line) or _PMID_LABEL.search(line)
        if doi:
            out.append(_entry(doi=doi))
        elif m:
            out.append(_entry(pmid=m.group(m.lastindex or 0)))
    return out


def _bibtex_fields(body: str) -> dict[str, str]:
    """Fields of a BibTeX entry: ``name = {…}`` | "…" | number (balanced braces)."""
    fields, i, n = {}, 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([\w-]+)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] in '{"':
            quote_, depth, j = body[i], 0, i
            while j < n:
                c = body[j]
                if c == "\\":
                    j += 2
                    continue
                if quote_ == "{" and c in "{}":
                    depth += 1 if c == "{" else -1
                    if depth == 0:
                        break
                elif quote_ == '"' and c == '"' and j > i:
                    break
                j += 1
            value, i = body[i + 1 : j], j + 1
        else:
            m2 = re.compile(r"[^,\s]*").match(body, i)
            value, i = m2.group(0), m2.end()
        fields[name] = value
    return fields


def _tex(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\\([&%$#_])", r"\1", value)
    value = re.sub(r"\\[a-zA-Z]+\s*", "", value).replace("{", "").replace("}", "")
    return _clean(value)


def parse_bibtex(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@(\w+)\s*\{", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        body = text[m.end() : j - 1]
        f = _bibtex_fields(body.partition(",")[2])
        authors = [_author(a) for a in re.split(r"\s+and\s+", _tex(f.get("author")) or "") if a]
        out.append(
            _entry(
                doi=f.get("doi") or f.get("url"),
                pmid=f.get("pmid")
                or (f.get("eprint") if "pubmed" in f.get("eprinttype", "") else None),
                title=_tex(f.get("title")),
                authors=authors or None,
                journal=_tex(f.get("journal") or f.get("journaltitle")),
                year=_year(f.get("year") or f.get("date")),
                abstract=_tex(f.get("abstract")),
                language=f.get("language") or f.get("langid"),
            )
        )
    return out


def parse_ris(text: str) -> list[dict]:
    out, rec = [], {}

    def flush() -> None:
        if rec:
            tags = lambda *k: [v for key in k for v in rec.get(key, [])]  # noqa: E731
            extra = " ".join(tags("AN", "N1", "C2", "M1"))
            m = _PMID_LABEL.search(extra)
            out.append(
                _entry(
                    doi=next((v for v in tags("DO", "M3", "UR", "L3") if clean_doi(v)), None),
                    pmid=m.group(1) if m else None,
                    title=" ".join(tags("TI", "T1")[:1]),
                    authors=[_author(a) for a in tags("AU", "A1")] or None,
                    journal=next(iter(tags("JO", "JF", "T2", "JA")), None),
                    year=_year(next(iter(tags("PY", "Y1", "DA")), None)),
                    abstract=" ".join(tags("AB", "N2")[:1]),
                    language=next(iter(tags("LA")), None),
                )
            )
        rec.clear()

    for raw in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", raw.lstrip("\ufeff"))
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "ER":
            flush()
        elif tag == "TY" and rec:
            flush()
            rec.setdefault(tag, []).append(val)
        else:
            rec.setdefault(tag, []).append(val)
    flush()
    return out


def parse_csl_json(text: str) -> list[dict]:
    data = json.loads(text)
    items = data if isinstance(data, list) else data.get("items", [data])
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        authors = [
            _author(
                f"{a.get('family', '')}, {a.get('given', '')}"
                if a.get("family")
                else a.get("literal", "")
            )
            for a in it.get("author", [])
        ]
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append(
            _entry(
                doi=it.get("DOI"),
                pmid=it.get("PMID"),
                title=it.get("title"),
                authors=authors or None,
                journal=it.get("container-title"),
                year=parts[0] if parts and isinstance(parts[0], int) else None,
                abstract=it.get("abstract"),
                language=it.get("language"),
            )
        )
    return out


def parse_csv(text: str) -> list[dict]:
    """Zotero CSV export (columns Title, DOI, Author, Publication Title, Publication Year…)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        r = {k.strip().lower(): (v or "") for k, v in row.items() if k}
        authors = [_author(a) for a in re.split(r"\s*;\s*", r.get("author", "")) if a.strip()]
        out.append(
            _entry(
                doi=r.get("doi"),
                pmid=r.get("pmid"),
                title=r.get("title"),
                authors=authors or None,
                journal=r.get("publication title") or r.get("journal"),
                year=_year(r.get("publication year") or r.get("year") or r.get("date")),
                abstract=r.get("abstract note") or r.get("abstract"),
                language=r.get("language"),
            )
        )
    return out


def parse_references(content: str) -> tuple[str, list[dict]]:
    """Detects the format → (format, usable entries: at least one DOI, PMID or title)."""
    text = content.lstrip("\ufeff").strip()
    if text[:1] in "[{":
        fmt, parse = "csl", parse_csl_json
    elif re.search(r"^TY  - ", text, re.M):
        fmt, parse = "ris", parse_ris
    elif re.search(r"^\s*@\w+\s*\{", text, re.M):
        fmt, parse = "bibtex", parse_bibtex
    elif re.match(
        r"\s*\"?(key|item type|title|doi)\"?\s*,", text.splitlines()[0] if text else "", re.I
    ):
        fmt, parse = "csv", parse_csv
    else:
        fmt, parse = "list", parse_plain
    entries = parse(text)
    return fmt, [e for e in entries if e["doi"] or e["pmid"] or e["title"]]


def dedupe(entries: list[dict]) -> list[dict]:
    """One entry per DOI / PMID / title (the first; missing fields are filled in)."""
    seen: dict[str, dict] = {}
    out = []
    for e in entries:
        keys = [
            k for k in (e["doi"], e["pmid"] and f"pmid:{e['pmid']}", norm_title(e["title"])) if k
        ]
        first = next((seen[k] for k in keys if k in seen), None)
        if first is not None:
            for k, v in e.items():
                first[k] = first.get(k) or v
            continue
        out.append(e)
        for k in keys:
            seen[k] = e
    return out


def label(e: dict) -> str:
    return e.get("doi") or (e.get("pmid") and f"PMID {e['pmid']}") or (e.get("title") or "")[:120]


# --- resolution --------------------------------------------------------------


async def epmc_pmids_by_doi(dois: list[str], http: httpx.AsyncClient) -> dict[str, str]:
    """DOI → PMID, in batches (Europe PMC); DOIs without a PubMed record are absent."""
    dois = [d for d in dois if re.fullmatch(r"[^\s\"\\]+", d)]
    found: dict[str, str] = {}
    for i in range(0, len(dois), 25):
        batch = dois[i : i + 25]
        data = await _get_json(
            http,
            EPMC_SEARCH,
            {
                "query": "(" + " OR ".join(f'DOI:"{d}"' for d in batch) + ") AND SRC:MED",
                "format": "json",
                "resultType": "lite",
                "pageSize": 100,
            },
        )
        for row in ((data or {}).get("resultList") or {}).get("result") or []:
            if row.get("pmid") and row.get("doi"):
                found[row["doi"].lower()] = row["pmid"]
    return found


async def epmc_pmid_by_title(title: str, http: httpx.AsyncClient) -> str | None:
    """PMID of a nearly identical title (≥ 90%), else None: never a neighboring article."""
    q = re.sub(r"[\"\\]", " ", title)
    data = await _get_json(
        http,
        EPMC_SEARCH,
        {
            "query": f'TITLE:"{q}" AND SRC:MED',
            "format": "json",
            "resultType": "lite",
            "pageSize": 3,
        },
    )
    for row in ((data or {}).get("resultList") or {}).get("result") or []:
        ratio = SequenceMatcher(None, norm_title(title), norm_title(row.get("title"))).ratio()
        if row.get("pmid") and ratio >= 0.9:
            return row["pmid"]
    return None


async def crossref(doi: str, email: str, http: httpx.AsyncClient) -> dict | None:
    """Crossref record of a DOI → normalized entry, or None (unknown or unavailable)."""
    data = await _get_json(http, f"{CROSSREF}/{quote(doi, safe='/')}", {"mailto": email})
    msg = (data or {}).get("message")
    if not msg:
        return None
    authors = [
        _author(f"{a.get('family', '')}, {a.get('given', '')}")
        for a in msg.get("author", [])
        if a.get("family")
    ]
    parts = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0]
    return _entry(
        doi=msg.get("DOI") or doi,
        title=(msg.get("title") or [None])[0],
        authors=authors or None,
        journal=(msg.get("container-title") or [None])[0],
        year=parts[0] if parts and isinstance(parts[0], int) else None,
        abstract=msg.get("abstract"),
        language=msg.get("language"),
    )
