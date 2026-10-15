"""Routes projets : création, détails, import PubMed, articles, jobs (Module 2).

Authorization : ``_require_project_member`` → 403 si l'user n'est pas membre du
projet (LEAD/ANNOTATOR) et n'est pas ADMIN global.

Les jobs d'import sont persistés dans la table ``import_jobs`` (M3) : la
requête PubMed, le statut et le résultat/erreur y sont conservés (MODULE 6,
reproductibilité). Pas de dict in-memory, pas d'appel ``AsyncResult`` bloquant.
"""

import json
import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.article_text import DEFAULT_KINDS, KINDS
from app.celery_app import celery_app
from app.cleanup import delete_projects
from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    ImportJob,
    ImportJobStatus,
    LanguageCode,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    User,
    UserRole,
)
from app.inference.scoring import DEFAULT_WINDOWS
from app.pubmed import NCBIError, search_volume
from app.schema import DEFAULT_QUESTION, check_fields, project_fields
from app.settings.service import get_setting
from app.tasks import complete_fulltext_task, import_pubmed_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# M-1/M-2 : rate limiting par IP (fenêtre 15 min) sur les endpoints NCBI — anti
# DoS applicatif, même pattern que l'anti brute-force auth (F7).
IMPORT_RATE_LIMIT = 5
QUERY_VOLUME_RATE_LIMIT = 20


class ProjectCreate(BaseModel):
    # m8 : strip + refus du blanc (min_length après strip).
    title: str = Field(min_length=1)
    description: str | None = None
    default_language: LanguageCode = LanguageCode.EN
    text_sections: list[str] | None = None

    @field_validator("text_sections")
    @classmethod
    def _check_sections(cls, v: list[str] | None) -> list[str] | None:
        return _validate_kinds(v)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


def _validate_kinds(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    bad = [k for k in v if k != "all" and k not in KINDS]
    if bad or not v:
        raise ValueError(f"text_sections must be non-empty, values in {['all', *KINDS]}")
    return list(dict.fromkeys(v))


class ProjectUpdate(BaseModel):
    text_sections: list[str] | None = None
    question: str | None = Field(default=None, max_length=2000)
    fields: list[dict] | None = None
    read_windows: int | None = Field(default=None, ge=1, le=32)

    @field_validator("text_sections")
    @classmethod
    def _check_sections(cls, v: list[str] | None) -> list[str] | None:
        return _validate_kinds(v)

    @field_validator("fields")
    @classmethod
    def _check_fields(cls, v: list[dict] | None) -> list[dict] | None:
        return None if v is None else check_fields(v)


class ImportRequest(BaseModel):
    # m8 : bornage de la requête PubMed (max 2000 caractères).
    query: str = Field(min_length=1, max_length=2000)


def _make_http_client() -> httpx.AsyncClient:
    """Client httpx pour les appels NCBI (timeout explicite, m4)."""
    return httpx.AsyncClient(timeout=60.0)


async def _require_project_member(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> Project:
    """Charge le projet ; 404 si absent, 403 si l'user n'est ni membre ni ADMIN global."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role == UserRole.ADMIN:
        return project
    member = await session.get(ProjectMember, (project_id, user.id))
    if member is None:
        raise HTTPException(status_code=403, detail="Not a project member")
    return project


def review_payload(ann: HumanAnnotation | None, user: User | None) -> dict | None:
    """Dernière relecture humaine : qui, quand, statut (VALIDATED / CORRECTED / MANUAL)."""
    if ann is None:
        return None
    return {
        "by": (user.full_name or user.email) if user else None,
        "at": ann.created_at.isoformat() if ann.created_at else None,
        "status": (ann.decision_json or {}).get("review_status"),
    }


async def latest_reviews(db: AsyncSession, article_ids: list) -> dict:
    """{article_id: (annotation la plus récente, relecteur)} pour ces articles."""
    out: dict = {}
    if not article_ids:
        return out
    rows = await db.execute(
        select(HumanAnnotation, User)
        .join(User, User.id == HumanAnnotation.user_id, isouter=True)
        .where(HumanAnnotation.article_id.in_(article_ids))
        .order_by(HumanAnnotation.created_at.nulls_first(), HumanAnnotation.id)
    )
    for ann, u in rows:
        out[ann.article_id] = (ann, u)  # la plus récente écrase
    return out


def biblio_payload(a: Article) -> dict:
    """Date, journal, auteurs d'un article (premier / dernier auteur dérivés de la liste)."""
    authors = a.authors or []
    return {
        "journal": a.journal,
        "pub_date": a.pub_date,
        "authors": authors,
        "first_author": authors[0] if authors else None,
        "last_author": authors[-1] if len(authors) > 1 else None,
        "pub_types": a.pub_types or [],
        "doi_url": f"https://doi.org/{a.doi}" if a.doi else None,
        "fulltext_url": a.fulltext_url,
    }


def _answers(project: Project, decision: dict | None) -> list[dict]:
    """Réponses du modèle aux champs du projet, chacune avec sa propre certitude."""
    if not decision:
        return []
    conf = decision.get("field_confidence") or {}
    return [
        {
            "key": f["key"],
            "label": f["label"],
            "value": decision[f["key"]],
            "confidence": conf.get(f["key"]),
        }
        for f in project_fields(project)
        if f["key"] in decision
    ]


def _project_payload(
    project: Project, article_count: int | None = None, fulltext_count: int | None = None
) -> dict:
    payload = {
        "id": str(project.id),
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "created_by": str(project.created_by) if project.created_by else None,
        "default_language": project.default_language,
        "text_sections": project.text_sections or DEFAULT_KINDS,
        "question": project.question or DEFAULT_QUESTION,
        "read_windows": project.read_windows or DEFAULT_WINDOWS,
        "fields": project_fields(project),
        "custom_schema": project.fields is not None,
        "question_set": project.question is not None or project.fields is not None,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }
    if article_count is not None:
        payload["article_count"] = article_count
    if fulltext_count is not None:
        payload["fulltext_count"] = fulltext_count
    return payload


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, db: DbDep, user: UserDep) -> dict:
    """Crée un projet ; le créateur devient LEAD dans project_members."""
    project = Project(
        title=payload.title,
        description=payload.description,
        created_by=user.id,
        default_language=payload.default_language,
        text_sections=payload.text_sections,
    )
    db.add(project)
    await db.flush()  # assigne project.id (défaut Python uuid4) pour la membership
    db.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await db.commit()
    await db.refresh(project)  # status / created_at (server_default)
    return _project_payload(project)


@router.get("")
async def list_projects(db: DbDep, user: UserDep) -> dict:
    """Liste des projets de l'user (membre) ; un ADMIN global voit tous les projets.

    Chaque item inclut ``role_in_project`` (LEAD/ANNOTATOR, None si l'admin n'en
    est pas membre) et ``article_count`` — le frontend en a besoin pour la nav
    et les permissions.
    """
    # Memberships de l'user (project_id → role) : sert au filtrage (non-admin)
    # ET au champ role_in_project (admin inclus, s'il est aussi membre).
    memberships = (
        await db.execute(
            select(ProjectMember.project_id, ProjectMember.role_in_project).where(
                ProjectMember.user_id == user.id
            )
        )
    ).all()
    member_roles: dict[uuid.UUID, ProjectMemberRole] = dict(memberships)

    if user.role == UserRole.ADMIN:
        projects = (
            (await db.execute(select(Project).order_by(Project.created_at, Project.id)))
            .scalars()
            .all()
        )
    else:
        projects = (
            (
                await db.execute(
                    select(Project)
                    .where(Project.id.in_(list(member_roles.keys())))
                    .order_by(Project.created_at, Project.id)
                )
            )
            .scalars()
            .all()
        )

    items = []
    for project in projects:
        article_count = (
            await db.execute(
                select(func.count()).select_from(Article).where(Article.project_id == project.id)
            )
        ).scalar_one()
        payload = _project_payload(project, article_count=article_count)
        role = member_roles.get(project.id)
        payload["role_in_project"] = role.value if role is not None else None
        items.append(payload)
    return {"items": items}


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Détails du projet (status, nb articles, created_by)."""
    project = await _require_project_member(db, project_id, user)
    article_count = (
        await db.execute(
            select(func.count()).select_from(Article).where(Article.project_id == project.id)
        )
    ).scalar_one()
    fulltext_count = (
        await db.execute(
            select(func.count())
            .select_from(Article)
            .where(Article.project_id == project.id, Article.fulltext_source.is_not(None))
        )
    ).scalar_one()
    return _project_payload(project, article_count=article_count, fulltext_count=fulltext_count)


@router.patch("/{project_id}")
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, db: DbDep, user: UserDep
) -> dict:
    """Parties de texte, question et champs de réponse du projet (LEAD ou ADMIN)."""
    project = await _require_project_member(db, project_id, user)
    if user.role != UserRole.ADMIN:
        member = await db.get(ProjectMember, (project_id, user.id))
        if member is None or member.role_in_project != ProjectMemberRole.LEAD:
            raise HTTPException(status_code=403, detail="lead required")
    if payload.text_sections is not None:
        project.text_sections = payload.text_sections
    if payload.question is not None:
        project.question = payload.question.strip() or None
    if payload.fields is not None:
        project.fields = payload.fields
    if payload.read_windows is not None:
        project.read_windows = payload.read_windows
    await db.commit()
    return _project_payload(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    """Supprime un projet (creator/LEAD/ADMIN) + cascade (articles, jobs, etc.).

    Le delete est fait en SQL direct (``DELETE FROM projects``) : les FK
    ``project_id`` des tables liées sont ``ON DELETE CASCADE`` (modèle), donc la
    base nettoie articles/jobs/members/etc. sans que l'ORM charge chaque
    relation (plus rapide qu'un ``session.delete`` sur un gros projet).
    """
    project = await _require_project_member(db, project_id, user)
    # Seuls le creator, un LEAD, ou un ADMIN global peuvent supprimer.
    if user.role != UserRole.ADMIN and project.created_by != user.id:
        member = await db.get(ProjectMember, (project_id, user.id))
        if member is None or member.role_in_project != ProjectMemberRole.LEAD:
            raise HTTPException(
                status_code=403, detail="Only the creator or a LEAD can delete a project"
            )
    await delete_projects(db, [project_id])
    await db.commit()


@router.post(
    "/{project_id}/import",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("import", IMPORT_RATE_LIMIT))],
)
async def trigger_import(
    project_id: uuid.UUID, payload: ImportRequest, db: DbDep, user: UserDep
) -> dict:
    """Trigger l'import PubMed (task Celery). Renvoie 202 {job_id, status}.

    m1 : en mode eager le job est déjà terminé → le statut renvoyé est le réel
    (SUCCESS/FAILURE), pas toujours PENDING. M2 : 409 si le projet n'est pas
    DRAFT/FROZEN/COMPLETED : un import COMPLÈTE le corpus (doublons ignorés) ; seul un
    projet PROCESSING (import en cours) renvoie 409.
    M-2 : 409 si un import_job PENDING/RUNNING existe déjà (un import par
    projet à la fois, même en non-eager).
    """
    project = await _require_project_member(db, project_id, user)
    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="import already in progress")
    # M-2 : garde « un import par projet à la fois » (non-eager : le job reste
    # PENDING/RUNNING en base).
    active = (
        await db.execute(
            select(ImportJob.id)
            .where(
                ImportJob.project_id == project.id,
                ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="import already in progress")
    job = import_pubmed_task.delay(str(project.id), payload.query)
    if celery_app.conf.task_always_eager:
        status = job.state  # SUCCESS / FAILURE (le job est déjà terminé)
    else:
        status = "PENDING"
    return {"job_id": job.id, "status": status}


@router.post("/{project_id}/fulltext", status_code=202)
async def complete_fulltext(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Cherche le texte intégral manquant dans les sources ouvertes (Europe PMC, Unpaywall,
    OpenAlex, préprints). Tâche de fond ; progression via GET /jobs (kind=fulltext)."""
    project = await _require_project_member(db, project_id, user)
    if project.status not in (ProjectStatus.FROZEN, ProjectStatus.COMPLETED):
        raise HTTPException(status_code=409, detail="import not finished")
    active = (
        await db.execute(
            select(ImportJob.id)
            .where(
                ImportJob.project_id == project.id,
                ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="a job is already in progress")
    job = complete_fulltext_task.delay(str(project.id))
    return {"job_id": job.id, "status": "PENDING"}


@router.post(
    "/{project_id}/query-volume",
    dependencies=[Depends(_rate_limit_dependency("query_volume", QUERY_VOLUME_RATE_LIMIT))],
)
async def query_volume(
    project_id: uuid.UUID, payload: ImportRequest, db: DbDep, user: UserDep
) -> dict:
    """Volume de résultats PubMed pour la requête (MODULE 2 : calcul en temps
    réel du volume d'articles). Autorisation membre. Renvoie {count}.

    m-1 : erreur NCBI → 502 {error: ncbi_unavailable} (pas de 500, pas de
    clé/URL dans la réponse ; le détail propre est loggé côté serveur).
    """
    await _require_project_member(db, project_id, user)
    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    api_key = await get_setting(db, "ncbi_api_key")
    try:
        async with _make_http_client() as client:
            count = await search_volume(payload.query, api_key, client)
    except NCBIError as exc:
        # m-1 : le message NCBIError est propre (sans l'URL ni la clé) → loggé tel quel.
        logger.error("query-volume NCBI error: %s", exc)
        return JSONResponse(status_code=502, content={"error": "ncbi_unavailable"})
    return {"count": count}


def _answer_filters(project: Project, raw: str | None, pdec) -> list:
    """Conditions SQL « réponse du modèle au champ X = valeur » (JSON ``{clé: valeur}``)."""
    if not raw:
        return []
    try:
        wanted = json.loads(raw)
        assert isinstance(wanted, dict)
    except (ValueError, AssertionError):
        raise HTTPException(status_code=422, detail="answers must be a JSON object") from None
    types = {f["key"]: f["type"] for f in project_fields(project)}
    conds = []
    for key, value in wanted.items():
        if key not in types:
            raise HTTPException(status_code=422, detail=f"unknown field: {key}")
        if value is None or value == "":
            continue
        col = cast(pdec.op("->>")(key), String)  # SQLite renvoie 1/0 pour les booléens
        if types[key] == "text":
            esc = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(col.ilike(f"%{esc}%", escape="\\"))
        elif types[key] == "boolean":  # Postgres 'true'/'false' ; SQLite (tests) 1/0
            yes = value is True or str(value).lower() in ("true", "1")
            conds.append(col.in_(["true", "1"] if yes else ["false", "0"]))
        else:  # option de choix : égalité exacte
            conds.append(col == str(value))
    return conds


STATUS_PATTERN = "^(AUTO_ACCEPTED|AUTO_REJECTED|NEEDS_HUMAN_REVIEW|NONE|TO_REVIEW)$"


def article_filter(project: Project, status: str | None, answers: str | None):
    """Conditions de la liste des articles (statut IA + réponses) et sous-requêtes associées.

    → (where, pstatus, pconf, pdec, annotated). Partagé par la liste et les exports.
    """

    def latest(col):
        return (
            select(col)
            .where(ArticlePrediction.article_id == Article.id)
            .order_by(ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc())
            .limit(1)
            .scalar_subquery()
        )

    pstatus = latest(ArticlePrediction.status)
    pconf = latest(ArticlePrediction.confidence_score)
    pdec = latest(ArticlePrediction.decision_json)
    annotated = select(HumanAnnotation.id).where(HumanAnnotation.article_id == Article.id).exists()
    where = [Article.project_id == project.id]
    if status == "NONE":
        where.append(pstatus.is_(None))
    elif status == "TO_REVIEW":  # à relire : incertain ET pas encore annoté
        where += [pstatus == "NEEDS_HUMAN_REVIEW", ~annotated]
    elif status:
        where.append(pstatus == status)
    where += _answer_filters(project, answers, pdec)
    return where, pstatus, pconf, pdec, annotated


@router.get("/{project_id}/articles")
async def list_articles(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=200)] = 50,
    status: Annotated[str | None, Query(pattern=STATUS_PATTERN)] = None,
    sort: Annotated[str, Query(pattern="^(added|date_desc|date_asc|score_desc)$")] = "added",
    answers: Annotated[str | None, Query(max_length=4000)] = None,
) -> dict:
    """Articles du projet avec le statut de leur dernière prédiction.

    ``status`` filtre (``NONE`` = pas encore trié). ``answers`` = JSON ``{champ: valeur}`` :
    filtre sur la réponse du modèle (booléen exact, option exacte, texte contenu).
    Réponse : {items, total, page, per_page, stats} ; ``stats`` = articles par statut.
    """
    project = await _require_project_member(db, project_id, user)

    where, pstatus, pconf, pdec, annotated = article_filter(project, status, answers)
    total = (await db.execute(select(func.count()).select_from(Article).where(*where))).scalar_one()
    order = {
        "date_desc": [Article.pub_date.desc().nulls_last()],
        "date_asc": [Article.pub_date.asc().nulls_last()],
        "score_desc": [pconf.desc().nulls_last()],
        "added": [Article.created_at],
    }[sort] + [Article.id]
    rows = (
        await db.execute(
            select(
                Article,
                pstatus.label("pstatus"),
                pconf.label("pconf"),
                annotated.label("ann"),
                pdec.label("pdec"),
            )
            .where(*where)
            .order_by(*order)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    stats_rows = (
        await db.execute(
            select(pstatus.label("s"), func.count())
            .select_from(Article)
            .where(Article.project_id == project.id)
            .group_by("s")
        )
    ).all()
    stats = {(k or "NONE"): n for k, n in stats_rows}
    reviews = await latest_reviews(db, [r[0].id for r in rows])
    stats["TO_REVIEW"] = (
        await db.execute(
            select(func.count())
            .select_from(Article)
            .where(Article.project_id == project.id, pstatus == "NEEDS_HUMAN_REVIEW", ~annotated)
        )
    ).scalar_one()
    return {
        "items": [
            {
                "id": str(a.id),
                "pmid": a.pmid,
                "pmcid": a.pmcid,
                "doi": a.doi,
                "title": a.title,
                "abstract": a.abstract,
                "original_language": a.original_language,
                "has_fulltext": bool(a.sections),
                "fulltext_source": a.fulltext_source,
                **biblio_payload(a),
                "prediction_status": st,
                "confidence": conf,
                "answers": _answers(project, dec),
                "annotated": bool(ann),
                "review": review_payload(*reviews.get(a.id, (None, None))),
            }
            for a, st, conf, ann, dec in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "stats": stats,
    }


@router.get("/{project_id}/jobs")
async def list_jobs(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Statut des jobs d'import du projet (M3) : lu depuis ``import_jobs``.

    {jobs: [{id, task, status, query, result, error, created_at, completed_at}]}.
    """
    project = await _require_project_member(db, project_id, user)
    jobs = (
        (
            await db.execute(
                select(ImportJob)
                .where(ImportJob.project_id == project.id)
                .order_by(ImportJob.created_at, ImportJob.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "jobs": [
            {
                "id": str(j.id),
                "task": "import_pubmed",
                "kind": j.kind or "import",
                "status": j.status,
                "query": j.query,
                "result": j.result_json,
                "error": j.error,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ]
    }
