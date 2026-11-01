"""Project routes: creation, details, PubMed import, articles, jobs (Module 2).

Authorization: ``_require_project_member`` -> 403 if the user is not a member
of the project (LEAD/ANNOTATOR) and not a global ADMIN.

Import jobs are persisted in the ``import_jobs`` table (M3): the
PubMed query, the status and the result/error are kept there (MODULE 6,
reproducibility). No in-memory dict, no blocking ``AsyncResult`` call.
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import String, Text, cast, false, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.article_text import DEFAULT_KINDS, KINDS
from app.celery_app import celery_app
from app.cleanup import delete_projects
from app.core.config import get_settings
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
    Search,
    User,
    UserRole,
)
from app.europepmc import EpmcError
from app.openalex import OpenAlexError
from app.pubmed import NCBIError, _esearch_max
from app.refimport import dedupe, parse_references
from app.schema import DEFAULT_QUESTION, check_fields, project_fields
from app.searches import default_search, definition_hash, get_search, in_search, lot_condition
from app.settings.service import get_setting
from app.sources import DateBounds
from app.tasks import ADAPTERS, _source_key_setting, complete_fulltext_task, import_pubmed_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# M-1/M-2: rate limiting per IP (15-min window) on the NCBI endpoints -
# application DoS guard, same pattern as the auth anti brute-force (F7).
IMPORT_RATE_LIMIT = 5
QUERY_VOLUME_RATE_LIMIT = 20


class ProjectCreate(BaseModel):
    # m8: strip + refusal of blank (min_length after strip).
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
    # m8: bounding of the PubMed query (max 2000 characters).
    query: str = Field(min_length=1, max_length=2000)
    # Optional publication date bounds (ISO YYYY-MM-DD): ANDed into the
    # NCBI term ([dp]) and into the bisection bounds for large result sets (fewer esearch calls).
    date_from: date | None = None
    date_to: date | None = None
    source: str = "pubmed"  # queried database (adapters registry)

    @model_validator(mode="after")
    def _check_source(self) -> "ImportRequest":
        if self.source not in ADAPTERS:
            raise ValueError(f"unknown source: {self.source}")
        return self

    @model_validator(mode="after")
    def _check_date_bounds(self) -> "ImportRequest":
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must be before or equal to date_to")
        return self


def _make_http_client() -> httpx.AsyncClient:
    """httpx client for the NCBI calls (explicit timeout, m4)."""
    return httpx.AsyncClient(timeout=60.0)


async def _require_project_member(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> Project:
    """Loads the project; 404 if absent, 403 if the user is neither a member nor a global ADMIN."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role == UserRole.ADMIN:
        return project
    member = await session.get(ProjectMember, (project_id, user.id))
    if member is None:
        raise HTTPException(status_code=403, detail="Not a project member")
    return project


def _human(reviews: dict, article_id) -> dict | None:
    ann = reviews.get(article_id, (None, None))[0]
    return ann.decision_json if ann else None


def review_payload(ann: HumanAnnotation | None, user: User | None) -> dict | None:
    """Last human re-read: who, when, status (VALIDATED / CORRECTED / MANUAL)."""
    if ann is None:
        return None
    return {
        "by": (user.full_name or user.email) if user else None,
        "at": ann.created_at.isoformat() if ann.created_at else None,
        "status": (ann.decision_json or {}).get("review_status"),
    }


async def latest_reviews(
    db: AsyncSession, article_ids: list, search: Search, reviewer_id: uuid.UUID | None = None
) -> dict:
    """{article_id: (most recent annotation, re-reader)} of this search; with
    ``reviewer_id``, only that reviewer's re-reads (blind)."""
    out: dict = {}
    if not article_ids:
        return out
    own = [HumanAnnotation.user_id == reviewer_id] if reviewer_id else []
    rows = await db.execute(
        select(HumanAnnotation, User)
        .join(User, User.id == HumanAnnotation.user_id, isouter=True)
        .where(
            HumanAnnotation.article_id.in_(article_ids),
            in_search(HumanAnnotation.search_id, search),
            *own,
        )
        .order_by(HumanAnnotation.created_at.nulls_first(), HumanAnnotation.id)
    )
    for ann, u in rows:
        out[ann.article_id] = (ann, u)  # the most recent wins
    return out


def biblio_payload(a: Article) -> dict:
    """Date, journal, authors of an article (first/last author derived from the list)."""
    authors = a.authors or []
    return {
        "source": a.source,
        "native_id": a.native_id,
        "journal": a.journal,
        "pub_date": a.pub_date,
        "authors": authors,
        "first_author": authors[0] if authors else None,
        "last_author": authors[-1] if len(authors) > 1 else None,
        "pub_types": a.pub_types or [],
        "doi_url": f"https://doi.org/{a.doi}" if a.doi else None,
        "fulltext_url": a.fulltext_url,
    }


def _answers(src, decision: dict | None, human: dict | None = None) -> list[dict]:
    """Answers to the project's fields: those of the last human re-read when it exists
    (``source`` = human, confidence absent), else those of the model with their confidence."""
    human = human or {}
    if not decision and not human:
        return []
    decision = decision or {}
    conf = decision.get("field_confidence") or {}
    out = []
    for f in project_fields(src):
        k = f["key"]
        if k in human:
            out.append(
                {"key": k, "label": f["label"], "value": human[k], "confidence": None,
                 "source": "human"}
            )  # fmt: skip
        elif k in decision:
            out.append(
                {"key": k, "label": f["label"], "value": decision[k], "confidence": conf.get(k),
                 "source": "model"}
            )  # fmt: skip
    return out


def _project_payload(
    project: Project,
    article_count: int | None = None,
    fulltext_count: int | None = None,
    search: Search | None = None,
) -> dict:
    """The project; question, items and text sections are those of ``search`` when given
    (each search has its own), else those of the project."""
    src = search or project
    payload = {
        "id": str(project.id),
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "created_by": str(project.created_by) if project.created_by else None,
        "default_language": project.default_language,
        "text_sections": src.text_sections or DEFAULT_KINDS,
        "question": src.question or DEFAULT_QUESTION,
        "read_windows": src.read_windows,
        "fields": project_fields(src),
        "custom_schema": src.fields is not None,
        "question_set": src.question is not None or src.fields is not None,
        "search_id": str(search.id) if search else None,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        # Scheduled deletion for inactivity (None: none)
        "retention_delete_on": (
            (
                project.retention_warned_at
                + timedelta(days=get_settings().project_retention_grace_days)
            ).isoformat()
            if project.retention_warned_at
            else None
        ),
    }
    if article_count is not None:
        payload["article_count"] = article_count
    if fulltext_count is not None:
        payload["fulltext_count"] = fulltext_count
    return payload


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, db: DbDep, user: UserDep) -> dict:
    """Creates a project; the creator becomes LEAD in project_members."""
    if not user.may_create_projects:
        raise HTTPException(status_code=403, detail="project creation not allowed")
    project = Project(
        title=payload.title,
        description=payload.description,
        created_by=user.id,
        default_language=payload.default_language,
        text_sections=payload.text_sections,
    )
    db.add(project)
    await db.flush()  # assigns project.id (default Python uuid4) for the membership
    await default_search(db, project)
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
    """List of the user's projects (member); a global ADMIN sees all projects.

    Each item includes ``role_in_project`` (LEAD/ANNOTATOR, None if the admin is
    not a member) and ``article_count`` - the frontend needs them for the nav
    and the permissions.
    """
    # The user's memberships (project_id -> role): used for filtering (non-admin)
    # AND for the role_in_project field (admin included, if also a member).
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
async def get_project(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict:
    """Project details (status, article count, created_by); question of the search ``search``
    (default: the initial search).

    ``role_in_project`` = the current user's role (like ``list_projects``):
    the UI uses it for the permissions (create a search, invite, remove) -
    without it, every non-admin appears annotator (bug 2026-10-30: an invited LEAD
    saw "only a project lead can create searches")."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
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
    member = await db.get(ProjectMember, (project_id, user.id))  # None : ADMIN non membre
    payload = _project_payload(
        project, article_count=article_count, fulltext_count=fulltext_count, search=scope
    )
    payload["role_in_project"] = member.role_in_project.value if member is not None else None
    return payload


async def _has_results(db: AsyncSession, scope: Search) -> bool:
    """Does the search have predictions or re-reads?"""
    for model in (ArticlePrediction, HumanAnnotation):
        found = (
            await db.execute(select(model.id).where(in_search(model.search_id, scope)).limit(1))
        ).first()
        if found is not None:
            return True
    return False


@router.patch("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Text sections, question and answer items (LEAD or ADMIN): those of the search
    ``search`` (default the initial search, whose settings are also the project's)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    if user.role != UserRole.ADMIN:
        member = await db.get(ProjectMember, (project_id, user.id))
        if member is None or member.role_in_project != ProjectMemberRole.LEAD:
            raise HTTPException(status_code=403, detail="lead required")
    new_hash = definition_hash(
        payload.question.strip() or None if payload.question is not None else scope.question,
        payload.fields if payload.fields is not None else scope.fields,
    )
    if new_hash != scope.definition_hash and await _has_results(db, scope):
        # the question of an already analyzed or re-read search no longer changes:
        # we create another one
        raise HTTPException(status_code=409, detail="question_locked")
    targets = (
        [scope, project] if scope.is_default else [scope]
    )  # le projet suit sa recherche initiale
    for t in targets:
        if payload.text_sections is not None:
            t.text_sections = payload.text_sections
        if payload.question is not None:
            t.question = payload.question.strip() or None
        if payload.fields is not None:
            t.fields = payload.fields
        if payload.read_windows is not None:
            t.read_windows = payload.read_windows
    scope.definition_hash = definition_hash(scope.question, scope.fields)
    await db.commit()
    return _project_payload(project, search=scope)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    """Deletes a project (creator/LEAD/ADMIN) + cascade (articles, jobs, etc.).

    The delete is done in direct SQL (``DELETE FROM projects``): the
    ``project_id`` FKs of the linked tables are ``ON DELETE CASCADE`` (models), so
    the database cleans up articles/jobs/members/etc. without the ORM loading each
    relation (faster than a ``session.delete`` on a big project).
    """
    project = await _require_project_member(db, project_id, user)
    # Only the creator, a LEAD, or a global ADMIN can delete.
    if user.role != UserRole.ADMIN and project.created_by != user.id:
        member = await db.get(ProjectMember, (project_id, user.id))
        if member is None or member.role_in_project != ProjectMemberRole.LEAD:
            raise HTTPException(
                status_code=403, detail="Only the creator or a LEAD can delete a project"
            )
    await delete_projects(db, [project_id])
    await db.commit()


async def _require_lead_or_admin(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="project LEAD required")


async def _require_no_active_job(db: AsyncSession, project_id: uuid.UUID) -> None:
    """M-2: one import / one full-text search at a time per project (409 otherwise)."""
    active = (
        await db.execute(
            select(ImportJob.id)
            .where(
                ImportJob.project_id == project_id,
                ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="a job is already in progress")


@router.post(
    "/{project_id}/import",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("import", IMPORT_RATE_LIMIT))],
)
async def trigger_import(
    project_id: uuid.UUID, payload: ImportRequest, db: DbDep, user: UserDep
) -> dict:
    """Triggers the PubMed import (Celery task). Returns 202 {job_id, status}.

    m1: in eager mode the job is already done -> the returned status is the real one
    (SUCCESS/FAILURE), not always PENDING. M2: 409 if the project is not
    DRAFT/FROZEN/COMPLETED: an import COMPLETES the corpus (duplicates ignored); only a
    PROCESSING project (import in progress) returns 409.
    M-2: 409 if an import_job PENDING/RUNNING already exists (one import per
    project at a time, even in non-eager).
    """
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)  # an import brings up to 5,000 articles
    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="import already in progress")
    await _require_no_active_job(db, project.id)
    job = import_pubmed_task.delay(
        str(project.id),
        payload.query,
        date_from=payload.date_from,
        date_to=payload.date_to,
        source=payload.source,
    )
    if celery_app.conf.task_always_eager:
        status = job.state  # SUCCESS / FAILURE (the job is already finished)
    else:
        status = "PENDING"
    return {"job_id": job.id, "status": status}


class ReferencesRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5_000_000)  # file or pasted input
    dry_run: bool = False  # analysis only: how many entries, which identifiers


@router.post(
    "/{project_id}/import-references",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("import", IMPORT_RATE_LIMIT))],
)
async def import_references(
    project_id: uuid.UUID, payload: ReferencesRequest, db: DbDep, user: UserDep
) -> dict:
    """Imports a list of references (DOI/PMID, BibTeX, RIS, CSL JSON, CSV Zotero) as the corpus.

    ``dry_run``: returns the recognized format and the counts, without importing
    anything. Otherwise a background task (progress via GET /jobs); the full text is
    searched automatically afterwards.
    """
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    try:
        fmt, entries = parse_references(payload.content)
    except ValueError as exc:  # malformed JSON
        raise HTTPException(status_code=422, detail="unreadable references file") from exc
    entries = dedupe(entries)
    if not entries:
        raise HTTPException(status_code=422, detail="no reference found")
    if payload.dry_run:
        return {
            "format": fmt,
            "count": len(entries),
            "with_doi": sum(1 for e in entries if e["doi"]),
            "with_pmid": sum(1 for e in entries if e["pmid"]),
            "title_only": sum(1 for e in entries if not (e["doi"] or e["pmid"])),
            "max": _esearch_max(),
        }
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="import already in progress")
    await _require_no_active_job(db, project.id)
    job = import_pubmed_task.delay(str(project.id), f"references ({fmt}, {len(entries)})", entries)
    return {
        "job_id": job.id,
        "status": job.state if celery_app.conf.task_always_eager else "PENDING",
    }


@router.post("/{project_id}/keep")
async def keep_project(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """ "Keep this project": counts as an activity and cancels the deletion
    warning for inactivity."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    project.kept_at = datetime.now(UTC)
    project.retention_warned_at = None
    await db.commit()
    return {"kept_at": project.kept_at.isoformat()}


@router.post("/{project_id}/fulltext", status_code=202)
async def complete_fulltext(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Searches the missing full text in open sources (Europe PMC, Unpaywall,
    OpenAlex, preprints). Background task; progress via GET /jobs (kind=fulltext)."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    if project.status not in (ProjectStatus.FROZEN, ProjectStatus.COMPLETED):
        raise HTTPException(status_code=409, detail="import not finished")
    await _require_no_active_job(db, project.id)
    job = complete_fulltext_task.delay(str(project.id))
    return {"job_id": job.id, "status": "PENDING"}


@router.post(
    "/{project_id}/query-volume",
    dependencies=[Depends(_rate_limit_dependency("query_volume", QUERY_VOLUME_RATE_LIMIT))],
)
async def query_volume(
    project_id: uuid.UUID, payload: ImportRequest, db: DbDep, user: UserDep
) -> dict:
    """Result volume of the requested source (default PubMed) for the query.

    m-1: sanitized source error -> 502 {error: <source>_unavailable} (no
    500, no key/URL in the response; the clean detail is logged server-side).
    """
    await _require_project_member(db, project_id, user)
    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    api_key = await get_setting(db, _source_key_setting(payload.source))
    try:
        async with _make_http_client() as client:
            adapter = ADAPTERS[payload.source](client, api_key)
            count = await adapter.search_volume(
                payload.query, DateBounds(payload.date_from, payload.date_to)
            )
    except NCBIError as exc:
        # m-1: the NCBIError message is clean (no URL, no key) -> logged as-is.
        logger.error("query-volume NCBI error: %s", exc)
        return JSONResponse(status_code=502, content={"error": "ncbi_unavailable"})
    except EpmcError as exc:
        logger.error("query-volume Europe PMC error: %s", exc)
        return JSONResponse(status_code=502, content={"error": "europepmc_unavailable"})
    except OpenAlexError as exc:
        logger.error("query-volume OpenAlex error: %s", exc)
        return JSONResponse(status_code=502, content={"error": "openalex_unavailable"})
    return {"count": count, "max": _esearch_max()}


def _answer_filters(search: Search, raw: str | None, pdec, hdec) -> list:
    """SQL conditions "answer to field X = value" (JSON ``{key: value}``): the
    last human re-read's answer wins over the model's."""
    if not raw:
        return []
    try:
        wanted = json.loads(raw)
        assert isinstance(wanted, dict)
    except (ValueError, AssertionError):
        raise HTTPException(status_code=422, detail="answers must be a JSON object") from None
    types = {f["key"]: f["type"] for f in project_fields(search)}
    conds = []
    for key, value in wanted.items():
        if key not in types:
            raise HTTPException(status_code=422, detail=f"unknown field: {key}")
        if value is None or value == "":
            continue
        # SQLite returns 1/0 for booleans
        col = func.coalesce(cast(hdec.op("->>")(key), String), cast(pdec.op("->>")(key), String))
        if types[key] == "text":
            esc = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(col.ilike(f"%{esc}%", escape="\\"))
        elif types[key] == "boolean":  # Postgres 'true'/'false' ; SQLite (tests) 1/0
            yes = value is True or str(value).lower() in ("true", "1")
            conds.append(col.in_(["true", "1"] if yes else ["false", "0"]))
        else:  # choice option: exact equality
            conds.append(col == str(value))
    return conds


def search_blob():
    """Title, journal, PMID, native identifier, DOI and authors in a single accent-free
    and lowercase text.

    Same expression as the trigram index ``ix_articles_search_trgm`` (migrations 0028/0032):
    PostgreSQL uses it for the quick search, whatever the number of articles. The ``''``
    are inlined (``literal_column``), not parameters, otherwise the expression would no
    longer match the index.
    """
    empty, space = literal_column("''"), literal_column("' '")
    parts = [
        func.coalesce(Article.title, empty),
        func.coalesce(Article.journal, empty),
        func.coalesce(Article.pmid, empty),
        func.coalesce(Article.native_id, empty),
        func.coalesce(Article.doi, empty),
        func.coalesce(cast(Article.authors, Text), empty),
    ]
    joined = parts[0]
    for p in parts[1:]:
        joined = joined.op("||")(space).op("||")(p)
    return func.lower(func.immutable_unaccent(joined))


def text_conditions(q: str | None) -> list:
    """Quick search: each typed word must appear in the title, the authors, the journal,
    an identifier (PMID, native identifier) or the DOI, case- and accent-insensitive;
    the words accumulate (AND)."""
    conds = []
    blob = search_blob()
    for word in (q or "").split()[:8]:
        norm = func.lower(func.immutable_unaccent(word))  # same normalization on both sides
        esc = func.replace(func.replace(func.replace(norm, "\\", "\\\\"), "%", "\\%"), "_", "\\_")
        conds.append(
            blob.like(
                literal_column("'%'").op("||")(esc).op("||")(literal_column("'%'")), escape="\\"
            )
        )
    return conds


STATUS_PATTERN = "^(AUTO_ACCEPTED|AUTO_REJECTED|NEEDS_HUMAN_REVIEW|NONE|TO_REVIEW)$"


def article_filter(
    search: Search,
    status: str | None,
    answers: str | None,
    q: str | None = None,
    fulltext: bool | None = None,
):
    """Conditions of the article list of a search (AI status + answers) and the
    associated sub-queries: only the results and re-reads of THIS search count.

    -> (where, pstatus, pconf, pdec, annotated). Shared by the list and the exports.
    """

    def latest(col):
        return (
            select(col)
            .where(
                ArticlePrediction.article_id == Article.id,
                in_search(ArticlePrediction.search_id, search),
            )
            .order_by(ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc())
            .limit(1)
            .scalar_subquery()
        )

    pstatus = latest(ArticlePrediction.status)
    pconf = latest(ArticlePrediction.confidence_score)
    pdec = latest(ArticlePrediction.decision_json)
    hdec = (  # latest human review: its answers take precedence over the model's
        select(HumanAnnotation.decision_json)
        .where(
            HumanAnnotation.article_id == Article.id,
            in_search(HumanAnnotation.search_id, search),
        )
        .order_by(HumanAnnotation.created_at.desc().nulls_last(), HumanAnnotation.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    annotated = (
        select(HumanAnnotation.id)
        .where(
            HumanAnnotation.article_id == Article.id, in_search(HumanAnnotation.search_id, search)
        )
        .exists()
    )
    where = [lot_condition(search), *text_conditions(q)]
    if fulltext is not None:  # full text / abstract only (same criterion as the counts)
        where.append(
            Article.fulltext_source.is_not(None) if fulltext else Article.fulltext_source.is_(None)
        )
    if search.kind == "blind":
        # Blind sample: no criterion may reveal the AI's answers or
        # those of the other re-readers (else the filter serves as a per-article oracle).
        if answers:
            raise HTTPException(status_code=422, detail="answers filter unavailable (blind)")
        if status in ("AUTO_ACCEPTED", "AUTO_REJECTED", "NEEDS_HUMAN_REVIEW"):
            where.append(false())
        elif status == "TO_REVIEW":
            where.append(~annotated)
        return where, pstatus, pconf, pdec, annotated
    if status == "NONE":
        where.append(pstatus.is_(None))
    elif status == "TO_REVIEW":  # to review: uncertain AND not yet annotated
        where += [pstatus == "NEEDS_HUMAN_REVIEW", ~annotated]
    elif status:
        where.append(pstatus == status)
    where += _answer_filters(search, answers, pdec, hdec)
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
    search: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    fulltext: bool | None = None,
) -> dict:
    """Articles of a search's batch (default the initial search = the whole corpus) with
    the status of their last prediction in this search.

    ``status`` filters (``NONE`` = not yet triaged). ``answers`` = JSON ``{field: value}``:
    filter on the model's answer (exact boolean, exact option, contained text).
    Response: {items, total, page, per_page, stats}; ``stats`` = articles by status.
    """
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)

    where, pstatus, pconf, pdec, annotated = article_filter(scope, status, answers, q, fulltext)
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
            .where(lot_condition(scope))
            .group_by("s")
        )
    ).all()
    stats = {(k or "NONE"): n for k, n in stats_rows}
    # Blind: only HER OWN review is projected (review / answers / confidence); the latest
    # re-read by another member must never appear, even after her own.
    reviews = await latest_reviews(
        db,
        [r[0].id for r in rows],
        scope,
        reviewer_id=user.id if scope.kind == "blind" else None,
    )
    mine: set | None = None
    if scope.kind == "blind":  # show the AI only after HER OWN review
        mine = set(reviews)
    stats["TO_REVIEW"] = (
        await db.execute(
            select(func.count())
            .select_from(Article)
            .where(lot_condition(scope), pstatus == "NEEDS_HUMAN_REVIEW", ~annotated)
        )
    ).scalar_one()
    if mine is not None:  # blind: no distribution of the AI results
        stats = {"NONE": sum(stats.values()) - stats.get("TO_REVIEW", 0)}
        stats["TO_REVIEW"] = (
            await db.execute(
                select(func.count()).select_from(Article).where(lot_condition(scope), ~annotated)
            )
        ).scalar_one()

    def visible(a) -> bool:  # AI / others' data showable for this article?
        return mine is None or a.id in mine

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
                "origin": a.origin,
                "origin_seeds": a.origin_seeds,
                "has_fulltext": bool(a.sections),
                "fulltext_source": a.fulltext_source,
                **biblio_payload(a),
                "prediction_status": st if visible(a) else None,
                # the re-read's relevance (cursor) replaces the model's
                "confidence": (
                    (_human(reviews, a.id) or {}).get("confidence_score", conf)
                    if visible(a)
                    else None
                ),
                "answers": _answers(scope, dec, _human(reviews, a.id)) if visible(a) else [],
                "annotated": bool(ann),
                "review": review_payload(*reviews.get(a.id, (None, None))) if visible(a) else None,
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
    """Status of the project's import jobs (M3): read from ``import_jobs``.

    {jobs: [{id, task, status, query, result, error, created_at, completed_at}]}."""
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
                "source": j.source,
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
