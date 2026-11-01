"""RO-Crate 1.1 export of a project: raw material (articles, texts, PDFs) + results + method.

Zip layout:
ro-crate-metadata.json   JSON-LD graph (project, articles, authors, models, actions)
README.md · project.json (question, fields, import queries, models + fingerprints, prompt)
extraction.csv · results.xlsx   final extraction table
articles/<key>/metadata.json · fulltext.md (or abstract.md) · fulltext.pdf (if kept)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime

from app.core.config import get_settings
from app.inference import public_model_ref
from app.sources import source_name

CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
CONFORMS = "https://w3id.org/ro/crate/1.1"


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def neutralize(v):
    """Free text exported to a spreadsheet: an apostrophe prefixed to anything that would be
    read as a formula (CSV/XLSX injection: =DDE(...), =HYPERLINK(...), @SUM…)."""
    return "'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v


def article_key(a) -> str:
    """Stable folder name: pmid-123, else doi, else native id, else UUID.

    The DOI is preferred over the native id: it is the most stable cross-source identity
    (the same paper in pubmed + europepmc lands on the same key)."""
    if a.pmid:
        return f"pmid-{re.sub(r'[^0-9A-Za-z]', '', a.pmid)}"
    if a.doi:
        return "doi-" + re.sub(r"[^0-9A-Za-z]+", "-", a.doi).strip("-")[:60]
    if getattr(a, "native_id", None):
        prefix = getattr(a, "source", None) or "art"
        return f"{prefix}-{re.sub(r'[^0-9A-Za-z]+', '-', a.native_id).strip('-')}"[:60]
    return f"art-{str(a.id)[:8]}"


def assign_keys(articles) -> dict:
    """{article.id: key}, unique (suffix -2, -3… on collision)."""
    seen: dict[str, int] = {}
    out = {}
    for a in articles:
        k = article_key(a)
        seen[k] = seen.get(k, 0) + 1
        out[a.id] = k if seen[k] == 1 else f"{k}-{seen[k]}"
    return out


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reviewer_reference(project_id, annotator_hash: str | None) -> str | None:
    """Project-scoped reviewer pseudonym, derived from the salted digest kept at account
    deletion: same person → same reference within a project, with no link between two
    projects. ``None`` when the identity is no longer available."""
    if not annotator_hash:
        return None
    return "reviewer-" + sha256_json(["diggypaper-reviewer", str(project_id), annotator_hash])


def fulltext_markdown(a) -> str:
    """Title + abstract + full text sections, in Markdown."""
    out = [f"# {a.title or a.pmid or a.id}", ""]
    if a.abstract:
        out += ["## Abstract", "", a.abstract, ""]
    for s in a.sections or []:
        out += [f"## {s.get('title') or s.get('kind') or 'Section'}", "", s.get("text", ""), ""]
    return "\n".join(out)


def article_record(a, key: str, pred, annos: list, fields: list[dict]) -> dict:
    """metadata.json: identifiers, bibliography, AI answers (with confidences) and human answers."""
    au = a.authors or []
    dec = (pred.decision_json or {}) if pred else {}
    conf = dec.get("field_confidence") or {}
    final = annos[-1] if annos else None
    return {
        "key": key,
        "pmid": a.pmid,
        "pmcid": a.pmcid,
        "doi": a.doi,
        "source": a.source,
        "native_id": a.native_id,
        "title": a.title,
        "journal": a.journal,
        "pub_date": a.pub_date,
        "authors": au,
        "first_author": au[0] if au else None,
        "last_author": au[-1] if len(au) > 1 else None,
        "pub_types": a.pub_types or [],
        "language": a.original_language,
        "abstract": a.abstract,
        "fulltext_source": a.fulltext_source,
        "fulltext_url": a.fulltext_url,
        "ai": None
        if not pred
        else {
            "model": pred.model_used,
            "model_identifier": pred.model_identifier,
            "model_version": pred.model_version,
            "status": pred.status.value if pred.status else None,
            "relevance": pred.confidence_score,
            "answers": {f["key"]: dec.get(f["key"]) for f in fields},
            "certainty": {f["key"]: conf.get(f["key"]) for f in fields},
            "evidence_quotes": pred.evidence_quotes,
            "reasoning": dec.get("reasoning_summary"),
        },
        "human": [
            {
                "annotation_id": str(h.id),
                "created_at": h.created_at.isoformat() if h.created_at else None,
                "reviewer_ref": getattr(h, "reviewer_ref", None),
                "answers": {f["key"]: (h.decision_json or {}).get(f["key"]) for f in fields},
                "evidence_quotes": (h.decision_json or {}).get("evidence_quotes"),
                "notes": h.notes,
            }
            for h in annos
        ],
        "final_status": "ANNOTATED"
        if final
        else (pred.status.value if pred and pred.status else "NOT_TRIAGED"),
    }


def extraction_csv(articles, keys: dict, preds: dict, annos: dict, fields: list[dict]) -> bytes:
    """Flat extraction table (one row per article), UTF-8 with BOM for Excel."""
    head = [
        "key",
        "pmid",
        "doi",
        "source",
        "native_id",
        "title",
        "pub_date",
        "journal",
        "first_author",
        "last_author",
    ]
    head += ["status", "relevance", "fulltext_source", "model", "model_identifier", "model_version"]
    head += [f["key"] for f in fields] + [f"{f['key']}_certainty" for f in fields]
    head += [f"{f['key']}_human" for f in fields]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    for a in articles:
        r = article_record(a, keys[a.id], preds.get(a.id), annos.get(a.id, []), fields)
        ai, human = r["ai"] or {}, (r["human"] or [{}])[-1]
        w.writerow(
            neutralize(c)
            for c in [r["key"], r["pmid"], r["doi"], r["source"], r["native_id"], r["title"]]
            + [r["pub_date"], r["journal"]]
            + [r["first_author"], r["last_author"], r["final_status"], ai.get("relevance")]
            + [r["fulltext_source"], ai.get("model"), ai.get("model_identifier")]
            + [ai.get("model_version")]
            + [(ai.get("answers") or {}).get(f["key"]) for f in fields]
            + [(ai.get("certainty") or {}).get(f["key"]) for f in fields]
            + [(human.get("answers") or {}).get(f["key"]) for f in fields]
        )
    return b"\xef\xbb\xbf" + buf.getvalue().encode()


def project_info(project, fields, import_jobs, inf_job, models: dict, prompt) -> dict:
    """project.json: everything needed to redo the analysis (queries, models, prompt)."""

    def model_info(name, stage):
        snap = (inf_job.model_info or {}).get(stage)
        if snap:  # snapshot frozen at job start (no secret by construction)
            cfg = {k: snap.get(k) for k in ("name", "backend", "identifier")}
            if snap.get("endpoint"):  # same model on another server = different fingerprint
                cfg["endpoint"] = snap["endpoint"]
            return {**snap, "config_sha256": sha256_json(cfg)}
        m = models.get(name)  # old job: fall back to the live registry, without URL secret
        if not name or m is None:
            return {"name": name}
        backend = str(m.backend_type) if m.backend_type else None
        ident, endpoint = public_model_ref(backend, m.model_identifier)  # never the API key
        cfg = {"name": m.name, "backend": backend, "identifier": ident}
        if endpoint:
            cfg["endpoint"] = endpoint
        return {
            **cfg,
            "version": None,
            "config_sha256": sha256_json(cfg),
        }

    imports = [
        {
            "query": j.query,
            "source": j.source,
            "date": j.created_at.isoformat() if j.created_at else None,
            "imported": (j.result_json or {}).get("imported"),
        }
        for j in import_jobs
    ]
    return {
        "title": project.title,
        "description": project.description,
        "question": project.question,
        "fields": fields,
        "text_sections": project.text_sections,
        "read_windows": project.read_windows,
        "imports": imports,
        "analysis": None
        if inf_job is None
        else {
            "date": inf_job.created_at.isoformat() if inf_job.created_at else None,
            "status": inf_job.status.value if inf_job.status else None,
            "stage_1_model": model_info(inf_job.prefilter_model, "prefilter")
            if inf_job.prefilter_model
            else None,
            "stage_2_model": model_info(inf_job.model_used, "main"),
            "result": inf_job.result_json,
            "prompt": None
            if prompt is None
            else {
                "version": prompt.version_number,
                "system_prompt": prompt.system_prompt,
                "sha256": sha256_json(prompt.system_prompt),
            },
        },
    }


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()[:60] or "x"


def build_graph(project, articles, keys: dict, files: dict, info: dict, now: str) -> dict:
    """RO-Crate JSON-LD graph. ``files``: {article key: [file paths present]}."""
    top = ["README.md", "project.json", "extraction.csv", "results.xlsx"]
    top += ["references.ris", "references.bib", "prisma.svg"]
    mime = {
        "md": "text/markdown",
        "json": "application/json",
        "csv": "text/csv",
        "pdf": "application/pdf",
        "svg": "image/svg+xml",
        "ris": "application/x-research-info-systems",
        "bib": "application/x-bibtex",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }

    def file_entity(path, name, about=None):
        e = {
            "@id": path,
            "@type": "File",
            "name": name,
            "encodingFormat": mime[path.rsplit(".", 1)[-1]],
        }
        if about:
            e["about"] = {"@id": about}
        return e

    graph: list[dict] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": CONFORMS},
            "about": {"@id": "./"},
        }
    ]
    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": project.title,
        "description": project.question or project.description or project.title,
        "datePublished": now,
        "license": {"@id": get_settings().crate_license_url},
        "hasPart": [{"@id": p} for p in top],
        "mentions": [],
    }
    graph.append(root)
    graph.append(
        {
            "@id": get_settings().crate_license_url,
            "@type": "CreativeWork",
            "name": get_settings().crate_license_name,
        }
    )
    labels = {
        "README.md": "Content description",
        "project.json": "Project, queries, models and prompt",
        "extraction.csv": "Extraction table",
        "results.xlsx": "Full results (Excel)",
        "references.ris": "References (RIS: Zotero, EndNote, Mendeley)",
        "references.bib": "References (BibTeX)",
        "prisma.svg": "PRISMA 2020 flow diagram",
    }
    graph += [file_entity(p, labels[p]) for p in top]

    people: dict[str, str] = {}
    journals: dict[str, str] = {}
    for a in articles:
        key = keys[a.id]
        art_id = (
            f"https://doi.org/{a.doi}"
            if a.doi
            else f"https://pubmed.ncbi.nlm.nih.gov/{a.pmid}/"
            if a.pmid
            else f"#{key}"
        )
        names = []
        for n in a.authors or []:
            pid = people.setdefault(n, f"#person-{len(people) + 1}")
            names.append({"@id": pid})
        art = {"@id": art_id, "@type": "ScholarlyArticle", "name": a.title or key}
        if names:
            art["author"] = names
        if a.pub_date:
            art["datePublished"] = a.pub_date
        if a.journal:
            jid = journals.setdefault(a.journal, f"#journal-{_slug(a.journal)}")
            art["isPartOf"] = {"@id": jid}
        ids = [f"pmid:{a.pmid}" if a.pmid else None, f"pmcid:{a.pmcid}" if a.pmcid else None]
        art["identifier"] = [i for i in ids if i] + ([f"doi:{a.doi}"] if a.doi else [])
        graph.append(art)
        folder = f"articles/{key}/"
        present = files[key]
        graph.append(
            {
                "@id": folder,
                "@type": "Dataset",
                "name": a.title or key,
                "about": {"@id": art_id},
                "hasPart": [{"@id": folder + f} for f in present],
            }
        )
        root["hasPart"].append({"@id": folder})
        names_ = {
            "metadata.json": "Metadata, AI and human answers",
            "fulltext.md": "Full text",
            "abstract.md": "Abstract",
            "fulltext.pdf": "PDF",
        }
        graph += [file_entity(folder + f, names_[f], art_id) for f in present]

    graph += [{"@id": i, "@type": "Person", "name": n} for n, i in people.items()]
    graph += [{"@id": i, "@type": "Periodical", "name": n} for n, i in journals.items()]

    analysis = info.get("analysis")
    for i, q in enumerate(info["imports"], 1):
        graph.append(
            {
                "@id": f"#import-{i}",
                "@type": "SearchAction",
                "name": " ".join(filter(None, ["Import", source_name(q.get("source"))])),
                "query": q["query"],
                "startTime": q["date"],
                "result": {"@id": "extraction.csv"},
            }
        )
        root["mentions"].append({"@id": f"#import-{i}"})
    if analysis:
        apps = []
        for st in ("stage_1_model", "stage_2_model"):
            m = analysis.get(st)
            if m:
                mid = f"#model-{_slug(m['name'])}"
                apps.append({"@id": mid})
                ent = {
                    "@id": mid,
                    "@type": "SoftwareApplication",
                    "name": m["name"],
                    "softwareVersion": m.get("version") or m.get("identifier"),
                    "alternateName": m.get("identifier"),
                    "identifier": m.get("config_sha256"),
                }
                graph.append({k: v for k, v in ent.items() if v is not None})
        graph.append(
            {
                "@id": "#analysis",
                "@type": "CreateAction",
                "name": "Tri automatique des articles",
                "description": project.question,
                "startTime": analysis["date"],
                "instrument": apps,
                "object": {"@id": "project.json"},
                "result": [{"@id": "extraction.csv"}, {"@id": "results.xlsx"}],
            }
        )
        root["mentions"].append({"@id": "#analysis"})
    if not root["mentions"]:
        del root["mentions"]
    return {"@context": CRATE_CONTEXT, "@graph": graph}


def readme(project, n_articles: int, n_pdf: int, n_text: int) -> str:
    return f"""# {project.title}

Export RO-Crate (Diggypaper) — {datetime.now(UTC):%Y-%m-%d}.

{project.question or ""}

- `ro-crate-metadata.json` : JSON-LD description of the whole content
- `project.json` : question, fields, import queries (source, query, bounds), models
  (fingerprints), prompt
- `prisma.svg` : PRISMA 2020 flow diagram (counts also in `project.json`)
- `references.ris` / `references.bib` : references to import into Zotero, EndNote, JabRef…
- `extraction.csv` / `results.xlsx` : extraction table (AI answers, confidences, review)
- License: {get_settings().crate_license_name} for the metadata, answers and reviews;
  full texts and PDFs keep the license of their rights holders.
- `articles/<key>/` : one folder per article ({n_articles}) — `metadata.json`, `fulltext.md`
  ({n_text} with full text, otherwise `abstract.md`) and `fulltext.pdf` ({n_pdf} kept)

In `metadata.json`, each `human` entry keeps the annotator's identifier and the annotation
date, the answers, the notes and the recorded `evidence_quotes` (null if absent).
`reviewer_ref` is a project-local pseudonym, derived from the salted fingerprint used at
account deletion; it stays stable after that deletion if the e-mail and the salt have not
changed, and it is null if the identity is no longer available. No name or e-mail is added
to these entries (`results.xlsx` does contain the reviewers' names). These citations are
neither a source verification nor an evaluation of their support for the answers.
"""
