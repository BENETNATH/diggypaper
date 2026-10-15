"""Export RO-Crate 1.1 d'un projet : matière première (articles, textes, PDF) + résultats + méthode.

Structure du zip :
  ro-crate-metadata.json   graphe JSON-LD (projet, articles, auteurs, modèles, actions)
  README.md · project.json (question, champs, requêtes PubMed, modèles + empreintes, prompt)
  extraction.csv · results.xlsx   tableau d'extraction final
  articles/<clé>/metadata.json · fulltext.md (ou abstract.md) · fulltext.pdf (si conservé)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime

CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
CONFORMS = "https://w3id.org/ro/crate/1.1"


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def neutralize(v):
    """Texte libre exporté vers un tableur : une apostrophe devant tout ce qui serait lu comme
    une formule (injection CSV/XLSX : =DDE(...), =HYPERLINK(...), @SUM…)."""
    return "'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v


def article_key(a) -> str:
    """Nom de dossier stable : pmid-123, sinon doi, sinon début de l'UUID."""
    if a.pmid:
        return f"pmid-{re.sub(r'[^0-9A-Za-z]', '', a.pmid)}"
    if a.doi:
        return "doi-" + re.sub(r"[^0-9A-Za-z]+", "-", a.doi).strip("-")[:60]
    return f"art-{str(a.id)[:8]}"


def assign_keys(articles) -> dict:
    """{article.id: clé} unique (suffixe -2, -3… en cas de collision)."""
    seen: dict[str, int] = {}
    out = {}
    for a in articles:
        k = article_key(a)
        seen[k] = seen.get(k, 0) + 1
        out[a.id] = k if seen[k] == 1 else f"{k}-{seen[k]}"
    return out


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fulltext_markdown(a) -> str:
    """Titre + abstract + sections du texte intégral, en Markdown."""
    out = [f"# {a.title or a.pmid or a.id}", ""]
    if a.abstract:
        out += ["## Abstract", "", a.abstract, ""]
    for s in a.sections or []:
        out += [f"## {s.get('title') or s.get('kind') or 'Section'}", "", s.get("text", ""), ""]
    return "\n".join(out)


def article_record(a, key: str, pred, annos: list, fields: list[dict]) -> dict:
    """metadata.json : identifiants, bibliographie, réponses IA (avec certitudes) et humaines."""
    au = a.authors or []
    dec = (pred.decision_json or {}) if pred else {}
    conf = dec.get("field_confidence") or {}
    final = annos[-1] if annos else None
    return {
        "key": key,
        "pmid": a.pmid,
        "pmcid": a.pmcid,
        "doi": a.doi,
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
            "status": pred.status.value if pred.status else None,
            "relevance": pred.confidence_score,
            "answers": {f["key"]: dec.get(f["key"]) for f in fields},
            "certainty": {f["key"]: conf.get(f["key"]) for f in fields},
            "evidence_quotes": pred.evidence_quotes,
            "reasoning": dec.get("reasoning_summary"),
        },
        "human": [
            {
                "answers": {f["key"]: (h.decision_json or {}).get(f["key"]) for f in fields},
                "notes": h.notes,
            }
            for h in annos
        ],
        "final_status": "ANNOTATED"
        if final
        else (pred.status.value if pred and pred.status else "NOT_TRIAGED"),
    }


def extraction_csv(articles, keys: dict, preds: dict, annos: dict, fields: list[dict]) -> bytes:
    """Tableau d'extraction plat (une ligne par article), UTF-8 avec BOM pour Excel."""
    head = ["key", "pmid", "doi", "title", "pub_date", "journal", "first_author", "last_author"]
    head += ["status", "relevance", "fulltext_source"]
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
            for c in [r["key"], r["pmid"], r["doi"], r["title"], r["pub_date"], r["journal"]]
            + [r["first_author"], r["last_author"], r["final_status"], ai.get("relevance")]
            + [r["fulltext_source"]]
            + [(ai.get("answers") or {}).get(f["key"]) for f in fields]
            + [(ai.get("certainty") or {}).get(f["key"]) for f in fields]
            + [(human.get("answers") or {}).get(f["key"]) for f in fields]
        )
    return b"\xef\xbb\xbf" + buf.getvalue().encode()


def project_info(project, fields, import_jobs, inf_job, models: dict, prompt) -> dict:
    """project.json : tout ce qu'il faut pour refaire l'analyse (requêtes, modèles, prompt)."""

    def model_info(name):
        m = models.get(name)
        if not name or m is None:
            return {"name": name}
        cfg = {
            "name": m.name,
            "backend": m.backend_type.value if m.backend_type else None,
            "identifier": m.model_identifier,  # jamais la clé API
        }
        return {**cfg, "config_sha256": sha256_json(cfg)}

    return {
        "title": project.title,
        "description": project.description,
        "question": project.question,
        "fields": fields,
        "text_sections": project.text_sections,
        "read_windows": project.read_windows,
        "pubmed_queries": [
            {
                "query": j.query,
                "date": j.created_at.isoformat() if j.created_at else None,
                "imported": (j.result_json or {}).get("imported"),
            }
            for j in import_jobs
        ],
        "analysis": None
        if inf_job is None
        else {
            "date": inf_job.created_at.isoformat() if inf_job.created_at else None,
            "status": inf_job.status.value if inf_job.status else None,
            "stage_1_model": model_info(inf_job.prefilter_model)
            if inf_job.prefilter_model
            else None,
            "stage_2_model": model_info(inf_job.model_used),
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
    """Graphe JSON-LD RO-Crate. ``files`` : {clé d'article: [chemins de fichiers présents]}."""
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
        "hasPart": [{"@id": p} for p in top],
        "mentions": [],
    }
    graph.append(root)
    labels = {
        "README.md": "Description du contenu",
        "project.json": "Projet, requêtes, modèles et prompt",
        "extraction.csv": "Tableau d'extraction",
        "results.xlsx": "Résultats complets (Excel)",
        "references.ris": "Références (RIS : Zotero, EndNote, Mendeley)",
        "references.bib": "Références (BibTeX)",
        "prisma.svg": "Diagramme de flux PRISMA 2020",
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
            "metadata.json": "Métadonnées, réponses IA et humaines",
            "fulltext.md": "Texte intégral",
            "abstract.md": "Abstract",
            "fulltext.pdf": "PDF",
        }
        graph += [file_entity(folder + f, names_[f], art_id) for f in present]

    graph += [{"@id": i, "@type": "Person", "name": n} for n, i in people.items()]
    graph += [{"@id": i, "@type": "Periodical", "name": n} for n, i in journals.items()]

    analysis = info.get("analysis")
    for i, q in enumerate(info["pubmed_queries"], 1):
        graph.append(
            {
                "@id": f"#import-{i}",
                "@type": "SearchAction",
                "name": "Import PubMed",
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
                graph.append(
                    {
                        "@id": mid,
                        "@type": "SoftwareApplication",
                        "name": m["name"],
                        "softwareVersion": m.get("identifier"),
                        "identifier": m.get("config_sha256"),
                    }
                )
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

- `ro-crate-metadata.json` : description JSON-LD de tout le contenu
- `project.json` : question, champs, requêtes PubMed exactes, modèles (empreintes), prompt
- `prisma.svg` : diagramme de flux PRISMA 2020 (effectifs aussi dans `project.json`)
- `references.ris` / `references.bib` : références à importer dans Zotero, EndNote, JabRef…
- `extraction.csv` / `results.xlsx` : tableau d'extraction (réponses IA, certitudes, relecture)
- `articles/<clé>/` : un dossier par article ({n_articles}) — `metadata.json`, `fulltext.md`
  ({n_text} avec texte intégral, sinon `abstract.md`) et `fulltext.pdf` ({n_pdf} conservés)
"""
