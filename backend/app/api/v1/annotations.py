"""Routes d'annotation (MODULE 5) — viewer d'annotation mobile-first.

- GET  /projects/{project_id}/articles/{article_id}            : article seul (methods_text)
- GET  /projects/{project_id}/articles/{article_id}/prediction : prédiction la plus récente
- POST /projects/{project_id}/articles/{article_id}/annotation : soumission d'annotation

Authorization : ``_require_project_member`` → 403 si l'user n'est pas membre du
projet (LEAD/ANNOTATOR) et n'est pas ADMIN global. L'article doit appartenir au
projet (404 sinon) — pas de fuite inter-projets.
"""

import asyncio
import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.projects import (
    _require_project_member,
    biblio_payload,
    latest_reviews,
    review_payload,
)
from app.article_text import compose_text
from app.db.models import Article, ArticlePrediction, HumanAnnotation, Project, User
from app.schema import validate_decision

router = APIRouter(prefix="/projects", tags=["annotations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# Schéma d'annotation (spec §5) — mêmes clés que le gold dataset.
_AnimalSex = Literal["MALE_ONLY", "FEMALE_ONLY", "BOTH_SEXES", "UNSPECIFIED"]


_META_KEYS = {"confidence_score", "evidence_quotes", "reasoning_summary", "review_status"}


class AnnotationCreate(BaseModel):
    use_ai_behavior: bool
    animal_sex: _AnimalSex
    confidence_score: float = Field(ge=0, le=1)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=100)
    reasoning_summary: str = Field(default="", max_length=5000)


async def _require_article(
    db: AsyncSession, project_id: uuid.UUID, article_id: uuid.UUID, user: User
) -> Article:
    """Charge l'article ; 403/404 si pas membre du projet, 404 si l'article n'y est pas.

    Le 403 (non membre) est levé AVANT toute vérification de l'article : un
    non-membre ne peut pas énumérer les articles d'un projet.
    """
    project = await _require_project_member(db, project_id, user)
    article = await db.get(Article, article_id)
    if article is None or article.project_id != project.id:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


def _article_payload(a: Article, kinds: list[str] | None = None) -> dict:
    return {
        "id": str(a.id),
        "pmid": a.pmid,
        "pmcid": a.pmcid,
        "doi": a.doi,
        "title": a.title,
        "abstract": a.abstract,
        "methods_text": a.methods_text,
        # Texte fourni aux modèles = parties choisies pour le projet (cf. text_sections).
        "text": compose_text(a, kinds),
        "has_fulltext": bool(a.sections),
        "fulltext_source": a.fulltext_source,
        **biblio_payload(a),
        "original_language": a.original_language,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _prediction_payload(p: ArticlePrediction) -> dict:
    return {
        "id": str(p.id),
        "article_id": str(p.article_id) if p.article_id else None,
        "prompt_template_id": str(p.prompt_template_id) if p.prompt_template_id else None,
        "model_used": p.model_used,
        "decision_json": p.decision_json,
        "confidence_score": p.confidence_score,
        "evidence_quotes": p.evidence_quotes,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/{project_id}/articles/{article_id}")
async def get_article(
    project_id: uuid.UUID, article_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Article seul (avec methods_text) pour le viewer d'annotation."""
    article = await _require_article(db, project_id, article_id, user)
    project = await db.get(Project, project_id)
    return _article_payload(article, project.text_sections if project else None)


@router.post("/{project_id}/articles/{article_id}/fulltext")
async def upload_fulltext(
    project_id: uuid.UUID, article_id: uuid.UUID, request: Request, db: DbDep, user: UserDep
) -> dict:
    """Dépôt manuel du PDF d'un article (accès institutionnel) : corps = octets du PDF.

    Le texte est extrait et découpé en sections ; il remplace le texte intégral existant.
    """
    from app.fulltext import MAX_PDF_BYTES, MIN_CHARS, pdf_to_sections, store_pdf

    article = await _require_article(db, project_id, article_id, user)
    data = await request.body()
    if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=422, detail="a PDF file (max 25 MB) is required")
    try:
        sections = await asyncio.to_thread(pdf_to_sections, data)
    except Exception:  # noqa: BLE001 - PDF corrompu / chiffré
        raise HTTPException(status_code=422, detail="unreadable PDF") from None
    if sum(len(s["text"]) for s in sections) < MIN_CHARS:
        raise HTTPException(status_code=422, detail="no text found in the PDF (scanned?)")
    article.sections = sections
    article.fulltext_source = "pdf:upload"
    article.fulltext_url = None
    await store_pdf(db, article.id, data)
    article.methods_text = " ".join(s["text"] for s in sections if s["kind"] == "methods") or None
    await db.commit()
    project = await db.get(Project, project_id)
    return _article_payload(article, project.text_sections if project else None)


@router.get("/{project_id}/articles/{article_id}/prediction")
async def get_prediction(
    project_id: uuid.UUID, article_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict | None:
    """Prédiction la plus récente de l'article (null si aucune)."""
    article = await _require_article(db, project_id, article_id, user)
    prediction = (
        (
            await db.execute(
                select(ArticlePrediction)
                .where(ArticlePrediction.article_id == article.id)
                .order_by(
                    ArticlePrediction.created_at.desc().nulls_last(),
                    ArticlePrediction.id.desc(),
                )
            )
        )
        .scalars()
        .first()
    )
    if prediction is None:
        return None
    return _prediction_payload(prediction)


@router.get("/{project_id}/articles/{article_id}/annotation")
async def get_annotation(
    project_id: uuid.UUID, article_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict | None:
    """Dernière relecture humaine de l'article (réponses + relecteur + statut), null si aucune."""
    article = await _require_article(db, project_id, article_id, user)
    ann, reviewer = (await latest_reviews(db, [article.id])).get(article.id, (None, None))
    if ann is None:
        return None
    return {
        "decision_json": ann.decision_json,
        "notes": ann.notes,
        **review_payload(ann, reviewer),
    }


def _review_status(decision: dict, prediction: ArticlePrediction | None) -> str:
    """VALIDATED = réponses de l'IA inchangées ; CORRECTED = modifiées ; MANUAL = sans IA."""
    pred = (prediction.decision_json or {}) if prediction else {}
    keys = [k for k in decision if k not in _META_KEYS and k in pred]
    if not prediction or not pred:
        return "MANUAL"
    return "VALIDATED" if all(decision[k] == pred[k] for k in keys) else "CORRECTED"


@router.post("/{project_id}/articles/{article_id}/annotation", status_code=201)
async def create_annotation(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    payload: Annotated[dict, Body()],
    db: DbDep,
    user: UserDep,
) -> dict:
    """Soumet une annotation humaine (MODULE 5).

    Le payload est stocké dans ``decision_json`` (schéma spec §5) ; le
    ``reasoning_summary`` est aussi conservé dans ``notes`` (colonne Text).
    """
    article = await _require_article(db, project_id, article_id, user)
    project = await db.get(Project, project_id)
    if project is not None and project.fields is not None:
        # Schéma propre au projet : champs définis + clés standard optionnelles.
        errors = validate_decision(project.fields, payload)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        decision = {f["key"]: payload[f["key"]] for f in project.fields}
        decision["confidence_score"] = payload.get("confidence_score", 1.0)
        decision["evidence_quotes"] = [str(q) for q in payload.get("evidence_quotes") or []][:100]
        decision["reasoning_summary"] = str(payload.get("reasoning_summary") or "")[:5000]
    else:
        try:
            decision = AnnotationCreate(**payload).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    latest = (
        (
            await db.execute(
                select(ArticlePrediction)
                .where(ArticlePrediction.article_id == article.id)
                .order_by(
                    ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc()
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    decision["review_status"] = _review_status(decision, latest)
    ann = HumanAnnotation(
        article_id=article.id,
        user_id=user.id,
        decision_json=decision,
        notes=decision["reasoning_summary"],
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return {
        "id": str(ann.id),
        "article_id": str(ann.article_id) if ann.article_id else None,
        "user_id": str(ann.user_id) if ann.user_id else None,
        "annotator_hash": ann.annotator_hash,
        "decision_json": ann.decision_json,
        "notes": ann.notes,
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        **review_payload(ann, user),
    }
