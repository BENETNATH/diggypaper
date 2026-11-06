"""Document upload routes: POST /projects/{project_id}/documents.

Batch upload of .txt / .pdf files as project articles. Raw body (no multipart),
one file per request. Deduplication by sha256.
"""

import asyncio
import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, rate_limit_user
from app.api.v1.projects import _require_lead_or_admin, _require_project_member
from app.db.models import Article, ArticleFile, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["documents"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


async def _upload_document(request: Request, db: AsyncSession, user: User, project_id: uuid.UUID):
    from app.fulltext import MAX_PDF_BYTES, pdf_to_sections, text_to_sections

    await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)

    data = await request.body()
    filename = request.query_params.get("filename", "unnamed")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ("txt", "pdf"):
        raise HTTPException(status_code=422, detail="only .txt or .pdf files are accepted")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=422, detail="file exceeds 25 MB limit")

    # Validation by extension
    methods_text: str | None = None
    if ext == "pdf":
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=422, detail="not a valid PDF (missing magic)")
        try:
            sections = await asyncio.to_thread(pdf_to_sections, data)
        except Exception:
            raise HTTPException(status_code=422, detail="unreadable PDF") from None
    else:  # txt
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        text = text.replace("\x00", "")
        lines = text.splitlines()
        sections = text_to_sections(lines)
        methods_text = text.strip() or None

    # Déduplication
    sha = hashlib.sha256(data).hexdigest()
    existing = (
        await db.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.origin == "upload",
                Article.origin_ref == sha,
            )
        )
    ).scalar_one_or_none()

    if existing:
        return {
            "id": str(existing.id),
            "title": existing.title,
            "has_fulltext": bool(existing.sections),
            "fulltext_source": existing.fulltext_source,
            "fulltext_note": existing.fulltext_note,
            "duplicate": True,
        }

    # PDF scanné : pas de texte → article créé sans sections
    if ext == "pdf" and not sections:
        art = Article(
            project_id=project_id,
            origin="upload",
            origin_ref=sha,
            title=filename.rsplit(".", 1)[0],
            sections=None,
            fulltext_source=None,
            fulltext_note="no_text_layer",
            methods_text=None,
        )
        db.add(art)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            dup = (
                await db.execute(
                    select(Article).where(
                        Article.project_id == project_id,
                        Article.origin == "upload",
                        Article.origin_ref == sha,
                    )
                )
            ).scalar_one()
            return {
                "id": str(dup.id),
                "title": dup.title,
                "has_fulltext": False,
                "fulltext_source": None,
                "fulltext_note": "no_text_layer",
                "duplicate": True,
            }
        db.add(ArticleFile(article_id=art.id, data=data))
        await db.commit()
        return {
            "id": str(art.id),
            "title": art.title,
            "has_fulltext": False,
            "fulltext_source": None,
            "fulltext_note": "no_text_layer",
            "needs_ocr": True,
            "duplicate": False,
        }

    # Normal : texte extrait, sections classifiées
    title = filename.rsplit(".", 1)[0]
    if ext == "pdf":
        methods_text = " ".join(s["text"] for s in sections) or None
    # else: methods_text déjà calculé dans le branch txt

    art = Article(
        project_id=project_id,
        origin="upload",
        origin_ref=sha,
        title=title,
        sections=sections,
        fulltext_source="upload",
        fulltext_note=None,
        methods_text=methods_text,
    )
    db.add(art)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        dup = (
            await db.execute(
                select(Article).where(
                    Article.project_id == project_id,
                    Article.origin == "upload",
                    Article.origin_ref == sha,
                )
            )
        ).scalar_one()
        return {
            "id": str(dup.id),
            "title": dup.title,
            "has_fulltext": bool(dup.sections),
            "fulltext_source": dup.fulltext_source,
            "fulltext_note": dup.fulltext_note,
            "duplicate": True,
        }
    db.add(ArticleFile(article_id=art.id, data=data))
    await db.commit()

    return {
        "id": str(art.id),
        "title": art.title,
        "has_fulltext": bool(sections),
        "fulltext_source": art.fulltext_source,
        "fulltext_note": art.fulltext_note,
        "methods_text": art.methods_text,
        "duplicate": False,
    }


@router.post(
    "/{project_id}/documents",
    dependencies=[Depends(rate_limit_user("upload_document", 30))],
)
async def upload_document(
    project_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: UserDep,
):
    return await _upload_document(request, db, user, project_id)


# ============================================================================
# OCR
# ============================================================================


def ocr_pdf(data: bytes) -> list[dict]:
    """Run Tesseract OCR on a PDF and return IMRaD sections.

    Rasterises pages with PyMuPDF (~300 dpi), runs Tesseract (eng+fre) per
    page, concatenates text, and splits via text_to_sections.
    """

    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    from app.fulltext import MAX_PDF_PAGES, MIN_CHARS, text_to_sections

    doc = fitz.open(stream=data, filetype="pdf")
    lines: list[str] = []
    for page in doc[:MAX_PDF_PAGES]:
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(img, lang="eng+fre")
        lines.extend(text.replace("\x00", "").splitlines())
    doc.close()

    sections = text_to_sections(lines)
    total_chars = sum(len(s["text"]) for s in sections)
    if total_chars < MIN_CHARS:
        raise HTTPException(status_code=422, detail="ocr_empty")
    return sections


@router.post("/{project_id}/articles/{article_id}/ocr")
async def run_ocr(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
):
    """Manual OCR on a scanned PDF article (no_text_layer). Synchronous."""

    await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)

    art = (
        await db.execute(
            select(Article).where(Article.id == article_id, Article.project_id == project_id)
        )
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(status_code=404, detail="Article not found")

    # Preconditions: must have a PDF file and be in no_text_layer state
    if art.fulltext_note != "no_text_layer":
        raise HTTPException(
            status_code=409, detail="article already has text or is not marked for OCR"
        )

    af = (
        await db.execute(select(ArticleFile).where(ArticleFile.article_id == article_id))
    ).scalar_one_or_none()
    if af is None:
        raise HTTPException(status_code=409, detail="no file to OCR")

    # Run OCR (CPU-bound, offload to thread)
    sections = await asyncio.to_thread(ocr_pdf, af.data)

    # ocr_pdf raises 422 internally if below MIN_CHARS; guard here too
    if not sections:
        raise HTTPException(status_code=422, detail="ocr_empty")

    # Apply: replace sections + set source directly as "ocr"
    from app.fulltext import _apply_fulltext

    await _apply_fulltext(db, art, sections, "ocr", None, af.data)
    art.methods_text = " ".join(s["text"] for s in sections) or None
    await db.commit()

    return {
        "id": str(art.id),
        "title": art.title,
        "has_fulltext": True,
        "fulltext_source": "ocr",
        "fulltext_note": None,
    }
