"""Export XLSX d'un projet : corpus + résultats d'analyse + relecture humaine + paramètres."""

import asyncio
import io
import json
import os
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crate, prisma, references
from app.api.deps import get_current_user, get_db
from app.api.v1.projects import (
    STATUS_PATTERN,
    _require_lead_or_admin,
    _require_project_member,
    article_filter,
)
from app.db.models import (
    Article,
    ArticleFile,
    ArticlePrediction,
    ExportJob,
    HumanAnnotation,
    ImportJob,
    InferenceJob,
    ModelRegistry,
    PromptTemplate,
    User,
)
from app.schema import project_fields
from app.searches import ScopedProject, default_search, get_search, in_search, lot_condition

router = APIRouter(prefix="/projects", tags=["export"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_dir() -> str:
    """Dossier des zips générés (volume partagé backend/worker)."""
    return os.environ.get("EXPORT_DIR", "/app/exports")


_MAX_CELL = 32000  # limite Excel : 32 767 caractères par cellule


def _cell(v):
    """Valeur compatible Excel : listes/dicts aplatis, texte nettoyé et tronqué."""
    if v is None or isinstance(v, bool | int | float):
        return v
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v)
    elif isinstance(v, dict):
        v = "; ".join(f"{k}: {x}" for k, x in v.items())
    return crate.neutralize(ILLEGAL_CHARACTERS_RE.sub("", str(v))[:_MAX_CELL])


def _sheet(ws, header: list[str], rows: list[list], widths: dict[int, int] | None = None) -> None:
    ws.append(header)
    for r in rows:
        ws.append([_cell(v) for v in r])
    fill = PatternFill("solid", fgColor="DDE7F5")
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), fill
        c.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    for i in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 18)


def build_workbook(project, articles, preds, annos, import_jobs, inf_job, user_email) -> bytes:
    fields = project_fields(project)
    wb = Workbook()

    # --- Résultats : une ligne par article -----------------------------------
    header = ["PMID", "PMCID", "DOI", "Titre", "Date de publication", "Journal"]
    header += ["Premier auteur", "Dernier auteur", "Auteurs", "Types de publication", "Langue"]
    header += ["Lien DOI", "Lien texte intégral / PDF"]
    header += ["Texte analysé", "Statut IA", "Pertinence IA", "Modèle", "Décision IA"]
    header += [f"IA · {f['label']}" for f in fields]
    header += [f"Certitude · {f['label']}" for f in fields]
    header += ["Extraits (IA)", "Raisonnement (IA)", "Annotations humaines", "Statut final"]
    header += ["Relu par", "Statut de relecture", "Relu le"]
    header += [f"Humain · {f['label']}" for f in fields] + ["Notes humaines", "Résumé (abstract)"]
    rows = []
    for a in articles:
        p, hs = preds.get(a.id), annos.get(a.id, [])
        pd_ = (p.decision_json or {}) if p else {}
        h = hs[-1] if hs else None
        hd = (h.decision_json or {}) if h else {}
        final = "ANNOTÉ" if h else (p.status.value if p and p.status else "NON TRIÉ")
        au = a.authors or []
        row = [a.pmid, a.pmcid, a.doi, a.title, a.pub_date, a.journal]
        row += [au[0] if au else None, au[-1] if len(au) > 1 else None, "; ".join(au)]
        row += ["; ".join(a.pub_types or []), a.original_language]
        row += [f"https://doi.org/{a.doi}" if a.doi else None, a.fulltext_url]
        row += [a.fulltext_source or ("abstract" if a.abstract else "—")]
        row += [p.status.value if p and p.status else None, p.confidence_score if p else None]
        row += [p.model_used if p else None, pd_.get("decision")]
        row += [pd_.get(f["key"]) for f in fields]
        row += [(pd_.get("field_confidence") or {}).get(f["key"]) for f in fields]
        row += [(p.evidence_quotes if p else None), pd_.get("reasoning_summary")]
        row += [len(hs), final]
        row += [
            getattr(h, "reviewer", None),
            hd.get("review_status"),
            f"{h.created_at:%Y-%m-%d %H:%M}" if h and h.created_at else None,
        ]
        row += [hd.get(f["key"]) for f in fields]
        row += [h.notes if h else None, a.abstract]
        rows.append(row)
    ws = wb.active
    ws.title = "Résultats"
    _sheet(ws, header, rows, {4: 60, 5: 12, 6: 28, 9: 40})
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (4, 9))
            # liens cliquables (DOI, texte intégral) ; jamais javascript:/file: etc.
            if c.column in (12, 13) and str(c.value or "").startswith(("http://", "https://")):
                c.hyperlink = c.value
                c.style = "Hyperlink"

    # --- Résumé ------------------------------------------------------------------
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[header.index("Statut final")]] = (
            counts.get(row[header.index("Statut final")], 0) + 1
        )
    ws2 = wb.create_sheet("Résumé")
    _sheet(ws2, ["Statut final", "Articles"], [[k, v] for k, v in sorted(counts.items())], {1: 28})
    ws2.append([])
    ws2.append(["Total", len(rows)])
    ws2.append(["Avec texte intégral", sum(1 for a in articles if a.sections)])

    # --- Projet / méthode (reproductibilité) -----------------------------------
    ws3 = wb.create_sheet("Projet")
    info = [
        ["Projet", project.title],
        ["Question", project.question],
        ["Parties du texte lues", ", ".join(project.text_sections or ["methods"])],
        ["Fenêtres de lecture", project.read_windows],
        ["Exporté le", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")],
        ["Exporté par", user_email],
        [],
        ["Champs de réponse", "Type / options"],
    ]
    info += [
        [f["label"], f"{f['type']} {', '.join(f.get('options') or [])}".strip()] for f in fields
    ]
    info += [[], ["Requêtes PubMed", "Date · articles importés"]]
    info += [
        [j.query, f"{j.created_at:%Y-%m-%d} · {(j.result_json or {}).get('imported', '')}"]
        for j in import_jobs
    ]
    if inf_job is not None:
        res = inf_job.result_json or {}
        info += [[], ["Dernière analyse", inf_job.created_at.strftime("%Y-%m-%d %H:%M")]]
        info += [
            ["Modèle (étage 2)", inf_job.model_used],
            ["Pré-filtre (étage 1)", inf_job.prefilter_model],
        ]
        info += [[k, v] for k, v in res.items()]
    for r in info:
        ws3.append([_cell(v) for v in r])
    ws3.column_dimensions["A"].width, ws3.column_dimensions["B"].width = 34, 90
    for c in ws3["A"]:
        c.font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _load(db: AsyncSession, project, where=None, search=None):
    """Articles + dernière prédiction / annotations par article + jobs, ceux de ``search`` (par
    défaut : toute la recherche initiale)."""
    search = search or await default_search(db, project)
    articles = (
        (
            await db.execute(
                select(Article)
                .where(*(where or [lot_condition(search)]))
                .order_by(Article.created_at, Article.id)
            )
        )
        .scalars()
        .all()
    )
    ids = [a.id for a in articles]
    preds: dict = {}
    annos: dict = {}
    if ids:
        for p in (
            await db.execute(
                select(ArticlePrediction)
                .where(
                    ArticlePrediction.article_id.in_(ids),
                    in_search(ArticlePrediction.search_id, search),
                )
                .order_by(ArticlePrediction.created_at.nulls_first(), ArticlePrediction.id)
            )
        ).scalars():
            preds[p.article_id] = p  # le plus récent écrase
        if search.kind == "blind":
            preds = {}  # échantillon aveugle : les réponses de l'IA ne sortent pas
        for h in (
            await db.execute(
                select(HumanAnnotation)
                .where(
                    HumanAnnotation.article_id.in_(ids),
                    in_search(HumanAnnotation.search_id, search),
                )
                .order_by(HumanAnnotation.created_at.nulls_first(), HumanAnnotation.id)
            )
        ).scalars():
            annos.setdefault(h.article_id, []).append(h)
        names = {
            u.id: u.full_name or u.email
            for u in (
                await db.execute(
                    select(User).where(
                        User.id.in_({h.user_id for hs in annos.values() for h in hs if h.user_id})
                    )
                )
            ).scalars()
        }
        for hs in annos.values():
            for h in hs:
                h.reviewer = names.get(h.user_id)  # nom du relecteur pour l'export
    import_jobs = (
        (
            await db.execute(
                select(ImportJob)
                .where(
                    ImportJob.project_id == project.id,
                    or_(ImportJob.kind.is_(None), ImportJob.kind != "fulltext"),
                )
                .order_by(ImportJob.created_at)
            )
        )
        .scalars()
        .all()
    )
    inf_job = (
        (
            await db.execute(
                select(InferenceJob)
                .where(
                    InferenceJob.project_id == project.id,
                    in_search(InferenceJob.search_id, search),
                )
                .order_by(InferenceJob.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return articles, preds, annos, import_jobs, inf_job


def _slug(project) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", project.title).strip("-")[:40] or "projet"


@router.get("/{project_id}/export.xlsx")
async def export_xlsx(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> Response:
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    articles, preds, annos, import_jobs, inf_job = await _load(db, project, search=scope)
    project = ScopedProject(project, scope)
    data = await asyncio.to_thread(
        build_workbook, project, articles, preds, annos, import_jobs, inf_job, user.email
    )
    return Response(
        data,
        media_type=_XLSX,
        headers={"Content-Disposition": f'attachment; filename="diggypaper-{_slug(project)}.xlsx"'},
    )


def _notes(articles, preds: dict, fields: list[dict]) -> dict:
    """Note par article : statut IA, pertinence et réponses (pour retrouver le tri dans Zotero)."""
    out = {}
    for a in articles:
        p = preds.get(a.id)
        if p is None:
            continue
        dec = p.decision_json or {}
        answers = "; ".join(f"{f['label']}: {dec[f['key']]}" for f in fields if f["key"] in dec)
        rel = f" ({round(p.confidence_score * 100)} %)" if p.confidence_score is not None else ""
        out[a.id] = f"Diggypaper : {p.status.value if p.status else ''}{rel}. {answers}".strip()
    return out


async def _references(project_id, db, user, status, answers, fmt: str, search=None) -> Response:
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    where = article_filter(scope, status, answers)[0]
    articles, preds, *_ = await _load(db, project, where, scope)
    project = ScopedProject(project, scope)
    notes = _notes(articles, preds, project_fields(project))
    if fmt == "ris":
        body, mime = references.to_ris(articles, notes), references.RIS_MIME
    else:
        body, mime = references.to_bibtex(articles, notes), references.BIBTEX_MIME
    return Response(
        body.encode("utf-8"),
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_slug(project)}.{fmt}"'},
    )


PRISMA_LANGS = "^(fr|en|de|es)$"


@router.get("/{project_id}/prisma")
async def get_prisma(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict:
    """Effectifs du diagramme de flux PRISMA 2020 d'une recherche."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    articles, preds, annos, import_jobs, _ = await _load(db, project, search=scope)
    return prisma.prisma_counts(articles, preds, annos, import_jobs)


@router.get("/{project_id}/prisma.svg")
async def get_prisma_svg(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    lang: Annotated[str | None, Query(pattern=PRISMA_LANGS)] = None,
    search: uuid.UUID | None = None,
) -> Response:
    """Diagramme PRISMA 2020 en SVG (langue : ``lang`` sinon celle du projet)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    articles, preds, annos, import_jobs, _ = await _load(db, project, search=scope)
    counts = prisma.prisma_counts(articles, preds, annos, import_jobs)
    svg = prisma.render_svg(counts, lang or project.default_language or "fr")
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="{_slug(project)}-prisma.svg"'},
    )


@router.get("/{project_id}/prisma.pdf")
async def get_prisma_pdf(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    lang: Annotated[str | None, Query(pattern=PRISMA_LANGS)] = None,
    search: uuid.UUID | None = None,
) -> Response:
    """Diagramme PRISMA 2020 en PDF vectoriel (même contenu que le SVG)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    articles, preds, annos, import_jobs, _ = await _load(db, project, search=scope)
    counts = prisma.prisma_counts(articles, preds, annos, import_jobs)
    pdf = prisma.svg_to_pdf(prisma.render_svg(counts, lang or project.default_language or "fr"))
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_slug(project)}-prisma.pdf"'},
    )


@router.get("/{project_id}/export.ris")
async def export_ris(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    status: Annotated[str | None, Query(pattern=STATUS_PATTERN)] = None,
    answers: Annotated[str | None, Query(max_length=4000)] = None,
    search: uuid.UUID | None = None,
) -> Response:
    """Références RIS (Zotero, EndNote, Mendeley…) ; mêmes filtres que la liste des articles."""
    return await _references(project_id, db, user, status, answers, "ris", search)


@router.get("/{project_id}/export.bib")
async def export_bibtex(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    status: Annotated[str | None, Query(pattern=STATUS_PATTERN)] = None,
    answers: Annotated[str | None, Query(max_length=4000)] = None,
    search: uuid.UUID | None = None,
) -> Response:
    """Références BibTeX ; mêmes filtres que la liste des articles."""
    return await _references(project_id, db, user, status, answers, "bib", search)


async def write_crate(
    db: AsyncSession, project, user_email: str, tmp, progress=None, search=None
) -> dict:
    """Écrit le RO-Crate (zip) dans le fichier binaire ``tmp`` → {articles, pdf, text}.

    ``progress(done, total)`` (async, optionnel) est appelé tous les 10 articles.
    """
    articles, preds, annos, import_jobs, inf_job = await _load(db, project, search=search)
    fields = project_fields(project)
    names = {n for n in (inf_job.model_used, inf_job.prefilter_model) if n} if inf_job else set()
    models = {}
    if names:
        rows = await db.execute(select(ModelRegistry).where(ModelRegistry.name.in_(names)))
        models = {m.name: m for m in rows.scalars()}
    ptid = inf_job.prompt_template_id if inf_job else None
    prompt = await db.get(PromptTemplate, ptid) if ptid else None
    info = crate.project_info(project, fields, import_jobs, inf_job, models, prompt)
    keys = crate.assign_keys(articles)
    xlsx = await asyncio.to_thread(
        build_workbook, project, articles, preds, annos, import_jobs, inf_job, user_email
    )

    files: dict[str, list[str]] = {}
    n_pdf = n_text = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:

        def put(path: str, data: bytes | str, stored: bool = False) -> None:
            z.writestr(
                path,
                data,
                compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED,
            )

        for i, a in enumerate(articles, 1):
            key, folder = keys[a.id], f"articles/{keys[a.id]}/"
            rec = crate.article_record(a, key, preds.get(a.id), annos.get(a.id, []), fields)
            present = ["metadata.json"]
            put(folder + "metadata.json", json.dumps(rec, ensure_ascii=False, indent=2))
            if a.sections:
                n_text += 1
                present.append("fulltext.md")
                put(folder + "fulltext.md", crate.fulltext_markdown(a))
            elif a.abstract:
                present.append("abstract.md")
                put(folder + "abstract.md", crate.fulltext_markdown(a))
            pdf = (
                await db.execute(select(ArticleFile.data).where(ArticleFile.article_id == a.id))
            ).scalar_one_or_none()
            if pdf:
                n_pdf += 1
                present.append("fulltext.pdf")
                put(folder + "fulltext.pdf", pdf, stored=True)
            files[key] = present
            if progress and i % 10 == 0:
                await progress(i, len(articles))
        now = datetime.now(UTC).isoformat(timespec="seconds")
        counts = prisma.prisma_counts(articles, preds, annos, import_jobs)
        info["prisma"] = counts
        put("prisma.svg", prisma.render_svg(counts, project.default_language or "fr"))
        put("README.md", crate.readme(project, len(articles), n_pdf, n_text))
        put("project.json", json.dumps(info, ensure_ascii=False, indent=2))
        notes = _notes(articles, preds, fields)
        put("references.ris", references.to_ris(articles, notes))
        put("references.bib", references.to_bibtex(articles, notes))
        put("extraction.csv", crate.extraction_csv(articles, keys, preds, annos, fields))
        put("results.xlsx", xlsx)
        graph = crate.build_graph(project, articles, keys, files, info, now)
        put("ro-crate-metadata.json", json.dumps(graph, ensure_ascii=False, indent=2))
    return {"articles": len(articles), "pdf": n_pdf, "text": n_text}


def _export_payload(job: ExportJob) -> dict:
    return {
        "id": str(job.id),
        "status": job.status,
        "result": job.result_json,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "download": job.status == "COMPLETED" and bool(job.filename),
    }


async def _latest_export(db: AsyncSession, project_id: uuid.UUID, search=None) -> ExportJob | None:
    """Dernier export du projet (ou de cette recherche si ``search`` est donnée)."""
    cond = [ExportJob.project_id == project_id]
    if search is not None:
        cond.append(in_search(ExportJob.search_id, search))
    return (
        (
            await db.execute(
                select(ExportJob)
                .where(*cond)
                .order_by(ExportJob.created_at.desc(), ExportJob.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


@router.post("/{project_id}/export/crate", status_code=202)
async def start_crate_export(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict:
    """Lance l'export RO-Crate en tâche de fond (e-mail à la fin) ; un seul à la fois."""
    from app.tasks.export import export_task

    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    job = await _latest_export(db, project.id)
    scope = await get_search(db, project, search)
    if (
        job is not None
        and job.status in ("PENDING", "RUNNING")
        and (job.search_id or (await default_search(db, project)).id) != scope.id
    ):  # un export à la fois par projet : ne pas renvoyer celui d'une autre recherche sans le dire
        raise HTTPException(status_code=409, detail="another export is in progress")
    if job is None or job.status not in ("PENDING", "RUNNING"):
        job = ExportJob(
            project_id=project.id, user_id=user.id, search_id=scope.id, status="PENDING"
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        export_task.delay(str(job.id))
        await db.refresh(job)
    return _export_payload(job)


@router.get("/{project_id}/export/crate")
async def get_crate_export(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict | None:
    """Dernier export RO-Crate de la recherche (statut, progression, téléchargeable ?)."""
    project = await _require_project_member(db, project_id, user)
    job = await _latest_export(db, project_id, await get_search(db, project, search))
    return _export_payload(job) if job else None


@router.get("/{project_id}/export/crate/{job_id}/download")
async def download_crate(
    project_id: uuid.UUID, job_id: uuid.UUID, db: DbDep, user: UserDep
) -> FileResponse:
    project = await _require_project_member(db, project_id, user)
    job = await db.get(ExportJob, job_id)
    if job is None or job.project_id != project.id or job.status != "COMPLETED" or not job.filename:
        raise HTTPException(status_code=404, detail="export not found")
    path = Path(export_dir()) / job.filename
    if not path.is_file():  # purgé (plus de 7 jours) : à relancer
        raise HTTPException(status_code=410, detail="export expired")
    return FileResponse(
        path, media_type="application/zip", filename=f"{_slug(project)}-ro-crate.zip"
    )
