"""Tests for POST /api/v1/projects/{project_id}/documents (batch upload)."""

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Article, ArticleFile, Project, ProjectMember, ProjectMemberRole

BODY = "lorem ipsum dolor sit amet " * 200  # ~5000 chars


async def _make_project(session, project, researcher_user):
    """Commit project, then add researcher as LEAD."""
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()


# ============================================================================
# txt upload
# ============================================================================


@pytest.mark.asyncio
async def test_upload_txt_imrad(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    txt = (
        b"Abstract\nshort abstract.\n"
        b"Introduction\nwe did this.\n"
        b"Materials and Methods\nmice were housed.\n"
        b"Results\nthey ran.\n"
        b"References\n[1] x."
    )
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "my_paper.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "my_paper"
    assert data["has_fulltext"] is True
    assert data["fulltext_source"] == "upload"
    assert data["duplicate"] is False


@pytest.mark.asyncio
async def test_upload_txt_no_headings(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    txt = (BODY + "\n").encode()
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "plain.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "plain"
    assert data["has_fulltext"] is True
    assert data["methods_text"] is not None
    assert len(data["methods_text"]) > 100


@pytest.mark.asyncio
async def test_upload_pdf_creates_article(client, session, researcher_user, do_login):
    from tests.test_fulltext import make_pdf

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    pdf = make_pdf(["Methods"] + [BODY[i : i + 80] for i in range(0, 2400, 80)])
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "paper.pdf"},
        content=pdf,
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "paper"
    assert data["has_fulltext"] is True
    assert data["fulltext_source"] == "upload"
    # spec 2026-11-06-ux-refonte-v4 R2: page count (OCR estimate) on every PDF
    assert data["page_count"] == 1


# ============================================================================
# Déduplication
# ============================================================================


@pytest.mark.asyncio
async def test_upload_duplicate_returns_existing(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    txt = (BODY + "\n").encode()
    url = f"/api/v1/projects/{project.id}/documents"
    r1 = await client.post(
        url, params={"filename": "a.txt"}, content=txt, headers={"content-type": "text/plain"}
    )
    assert r1.status_code == 200
    first_id = r1.json()["id"]

    r2 = await client.post(
        url, params={"filename": "b.txt"}, content=txt, headers={"content-type": "text/plain"}
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["duplicate"] is True
    assert data["id"] == first_id


@pytest.mark.asyncio
async def test_same_content_different_projects(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    p1 = Project(title="P1", created_by=researcher_user.id)
    await _make_project(session, p1, researcher_user)
    p2 = Project(title="P2", created_by=researcher_user.id)
    session.add(p2)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=p2.id, user_id=researcher_user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()

    txt = (BODY + "\n").encode()
    r1 = await client.post(
        f"/api/v1/projects/{p1.id}/documents",
        params={"filename": "a.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    r2 = await client.post(
        f"/api/v1/projects/{p2.id}/documents",
        params={"filename": "a.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]


# ============================================================================
# Validation erreurs
# ============================================================================


@pytest.mark.asyncio
async def test_upload_wrong_extension_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    # .txt content sent as .pdf
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "fake.pdf"},
        content=b"hello world",
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_pdf_no_magic_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "bad.pdf"},
        content=b"not a pdf at all",
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_oversized_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    big = b"x" * (25 * 1024 * 1024 + 1)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "big.txt"},
        content=big,
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 422


# ============================================================================
# ArticleFile persisté
# ============================================================================


@pytest.mark.asyncio
async def test_upload_stores_article_file(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    txt = (BODY + "\n").encode()
    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "stored.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200
    art_id = uuid.UUID(resp.json()["id"])

    af = (
        await session.execute(select(ArticleFile).where(ArticleFile.article_id == art_id))
    ).scalar_one()
    assert af.data == txt


# ============================================================================
# PDF scanné (pas de texte)
# ============================================================================


@pytest.mark.asyncio
async def test_upload_scanned_pdf_creates_no_text_layer(client, session, researcher_user, do_login):
    from unittest.mock import patch

    from tests.test_fulltext import make_pdf

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    pdf = make_pdf([""])  # PDF with minimal content → simulates scanned
    with patch("app.fulltext.pdf_to_sections", return_value=[]):
        resp = await client.post(
            f"/api/v1/projects/{project.id}/documents",
            params={"filename": "scan.pdf"},
            content=pdf,
            headers={"content-type": "application/pdf"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_fulltext"] is False
    assert data["fulltext_note"] == "no_text_layer"
    assert data.get("needs_ocr") is True
    # spec 2026-11-06-ux-refonte-v4 R2: page count for the OCR confirmation panel
    assert data["page_count"] == 1


# ============================================================================
# Auth
# ============================================================================


@pytest.mark.asyncio
async def test_upload_requires_project_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    # Project created by the user, but NO ProjectMember row → 403
    project = Project(title="P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()

    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "a.txt"},
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_requires_lead_or_admin(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    # Member as ANNOTATOR (not lead)
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.ANNOTATOR,
        )
    )
    await session.commit()

    resp = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "a.txt"},
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 403


# ============================================================================
# OCR
# ============================================================================


@pytest.mark.asyncio
async def test_ocr_scanned_article_success(client, session, researcher_user, do_login):
    """OCR on a scanned article (no_text_layer) → sections filled."""
    from unittest.mock import patch

    from app.fulltext import text_to_sections

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    from tests.test_fulltext import make_pdf

    pdf = make_pdf([""])
    with patch("app.fulltext.pdf_to_sections", return_value=[]):
        r1 = await client.post(
            f"/api/v1/projects/{project.id}/documents",
            params={"filename": "scan.pdf"},
            content=pdf,
            headers={"content-type": "application/pdf"},
        )
    assert r1.status_code == 200
    art_id = r1.json()["id"]
    assert r1.json()["needs_ocr"] is True

    # Now run OCR on it (mock ocr_pdf to return valid sections)
    fake_lines = [
        "Abstract",
        "A short abstract.",
        "Introduction",
        "We did this.",
        "Results",
        "They ran.",
    ]
    fake_sections = text_to_sections(fake_lines)
    with patch("app.api.v1.documents.ocr_pdf", return_value=fake_sections):
        resp = await client.post(f"/api/v1/projects/{project.id}/articles/{art_id}/ocr")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_fulltext"] is True
    assert data["fulltext_source"] == "ocr"


@pytest.mark.asyncio
async def test_ocr_on_article_with_text_409(client, session, researcher_user, do_login):
    """OCR on an article that already has text → 409."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    txt = b"Introduction\nsome text.\nResults\nmore text.\n"
    r1 = await client.post(
        f"/api/v1/projects/{project.id}/documents",
        params={"filename": "a.txt"},
        content=txt,
        headers={"content-type": "text/plain"},
    )
    assert r1.status_code == 200
    art_id = r1.json()["id"]

    resp = await client.post(f"/api/v1/projects/{project.id}/articles/{art_id}/ocr")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_ocr_without_file_409(client, session, researcher_user, do_login):
    """OCR on an article with no ArticleFile → 409."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    art = Article(
        project_id=project.id,
        origin="upload",
        origin_ref="fakehash",
        title="NoFile",
        sections=None,
        fulltext_note="no_text_layer",
    )
    session.add(art)
    await session.commit()

    resp = await client.post(f"/api/v1/projects/{project.id}/articles/{art.id}/ocr")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_ocr_empty_result_422(client, session, researcher_user, do_login):
    """OCR returns too little text → 422 ocr_empty."""
    from unittest.mock import patch

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    await _make_project(session, project, researcher_user)

    from tests.test_fulltext import make_pdf

    pdf = make_pdf([""])
    with patch("app.fulltext.pdf_to_sections", return_value=[]):
        r1 = await client.post(
            f"/api/v1/projects/{project.id}/documents",
            params={"filename": "scan.pdf"},
            content=pdf,
            headers={"content-type": "application/pdf"},
        )
    art_id = r1.json()["id"]

    with patch("app.api.v1.documents.ocr_pdf", return_value=[]):
        resp = await client.post(f"/api/v1/projects/{project.id}/articles/{art_id}/ocr")
    assert resp.status_code == 422
    assert "ocr_empty" in resp.json()["detail"]


def test_ocr_raster_scale_bounds_pixel_memory():
    """Audit run-2 (needs_validation lead): the OCR rasterization scale must bound
    the TOTAL pixel memory, not only one dimension — a near-square page >= 4800 pt
    used to saturate the 20000 px per-dimension cap (20000x20000 px = 1.2 GB pixmap
    in the shared API process)."""
    from app.api.v1.documents import MAX_OCR_PIXELS, _raster_scale

    # a normal A4 page at 300 dpi is untouched
    assert _raster_scale(595, 842) == pytest.approx(300 / 72)
    # a huge near-square page is downsampled to the pixel budget, not 20000x20000
    s = _raster_scale(10000, 10000)
    raster = 10000 * s
    assert raster * raster <= MAX_OCR_PIXELS
    assert raster * raster < 20000 * 20000  # strictly below the old worst case
    # an elongated page still respects the per-dimension cap and the budget
    s = _raster_scale(20000, 5)
    assert 20000 * s <= MAX_OCR_PIXELS ** 0.5 * 1.01
