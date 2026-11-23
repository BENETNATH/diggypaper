"""Téléchargement de tous les PDF (ZIP) + nommage lisible partagé avec le RO-Crate."""

import io
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from conftest import LEGACY_FIELDS

from app.db.models import (
    Article,
    ArticleFile,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    SearchArticle,
    UserRole,
)
from app.references import pdf_filename
from app.searches import ensure_default_search
from tests.conftest import _make_user


def _art(**kw) -> SimpleNamespace:
    base = dict(authors=None, pub_year=None, title=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ============================================================================
# pdf_filename (partagé entre l'export ZIP et le RO-Crate)
# ============================================================================


def test_pdf_filename_two_authors_with_year():
    a = _art(authors=["Hussain A", "Zee B"], pub_year=2026, title="Acute nicotine")
    assert pdf_filename(a) == "hussain et al. 2026 Acute nicotine.pdf"


def test_pdf_filename_single_author_no_et_al():
    assert pdf_filename(_art(authors=["Doe J"], pub_year=2025, title="Mice")) == "doe 2025 Mice.pdf"


def test_pdf_filename_no_author_starts_with_title():
    assert pdf_filename(_art(title="Only a title")) == "Only a title.pdf"


def test_pdf_filename_without_year():
    assert pdf_filename(_art(authors=["Doe J"], title="Mice")) == "doe Mice.pdf"


def test_pdf_filename_truncates_title_on_word_boundary():
    title = " ".join(f"word{i:02d}" for i in range(20))  # 139 caractères
    a = _art(authors=["Doe J"], pub_year=2025, title=title)
    expected = "doe 2025 " + " ".join(f"word{i:02d}" for i in range(14)) + "….pdf"
    assert pdf_filename(a) == expected


def test_pdf_filename_strips_trailing_punctuation():
    assert pdf_filename(_art(authors=["Doe J"], title="Mice.")) == "doe Mice.pdf"
    # …et à la frontière de troncature (« word. » est le dernier mot complet avant 100)
    title = "a" * 90 + "word." + " " + "b" * 20
    assert pdf_filename(_art(title=title)) == "a" * 90 + "word….pdf"


def test_pdf_filename_sanitizes_filename_chars_and_collapses_spaces():
    a = _art(authors=["Doe J"], title='A/B:C*D?"<E>|F  G')
    assert pdf_filename(a) == "doe ABCDEF G.pdf"


def test_pdf_filename_collision_suffixes():
    a = _art(authors=["Doe J"], pub_year=2025, title="Mice")
    taken = set()
    assert pdf_filename(a, taken) == "doe 2025 Mice.pdf"
    assert pdf_filename(a, taken) == "doe 2025 Mice (2).pdf"
    assert pdf_filename(a, taken) == "doe 2025 Mice (3).pdf"
    assert taken == {"doe 2025 Mice.pdf", "doe 2025 Mice (2).pdf", "doe 2025 Mice (3).pdf"}


# ============================================================================
# GET /projects/{id}/export/pdfs
# ============================================================================


@pytest.fixture
async def lead_project(session, researcher_user, client, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(
        title="Revue test",
        created_by=researcher_user.id,
        question="Q ?",
        fields=LEGACY_FIELDS,
    )
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
    return project


def _pdfs_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/export/pdfs"


@pytest.mark.asyncio
async def test_export_pdfs_zip_lists_only_articles_with_pdf(client, session, lead_project):
    await ensure_default_search(session, lead_project)
    a1 = Article(
        project_id=lead_project.id,
        pmid="1",
        title="Mice",
        authors=["Doe J", "Roe R"],
        pub_year=2025,
    )
    a2 = Article(project_id=lead_project.id, pmid="2", title="No pdf here")
    a3 = Article(
        project_id=lead_project.id, pmid="3", title="Cats", authors=["Doe J"], pub_year=2025
    )
    session.add_all([a1, a2, a3])
    await session.flush()
    session.add_all(
        [
            ArticleFile(article_id=a1.id, data=b"%PDF-1.4 one"),
            ArticleFile(article_id=a3.id, data=b"%PDF-1.4 three"),
        ]
    )
    await session.commit()

    r = await client.get(_pdfs_url(lead_project))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="pdfs-Revue-test.zip"'
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None
    assert set(z.namelist()) == {"doe et al. 2025 Mice.pdf", "doe 2025 Cats.pdf"}
    assert z.read("doe et al. 2025 Mice.pdf") == b"%PDF-1.4 one"
    assert z.read("doe 2025 Cats.pdf") == b"%PDF-1.4 three"


@pytest.mark.asyncio
async def test_export_pdfs_collision_gets_numbered_suffix(client, session, lead_project):
    await ensure_default_search(session, lead_project)
    a1 = Article(
        project_id=lead_project.id, pmid="1", title="Mice", authors=["Doe J"], pub_year=2025
    )
    a2 = Article(
        project_id=lead_project.id, pmid="2", title="Mice", authors=["Doe J"], pub_year=2025
    )
    session.add_all([a1, a2])
    await session.flush()
    session.add_all(
        [
            ArticleFile(article_id=a1.id, data=b"%PDF-1.4 a"),
            ArticleFile(article_id=a2.id, data=b"%PDF-1.4 b"),
        ]
    )
    await session.commit()

    r = await client.get(_pdfs_url(lead_project))
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"doe 2025 Mice.pdf", "doe 2025 Mice (2).pdf"}
    assert {z.read(n) for n in z.namelist()} == {b"%PDF-1.4 a", b"%PDF-1.4 b"}


@pytest.mark.asyncio
async def test_export_pdfs_404_when_no_pdf_in_scope(client, session, lead_project):
    await ensure_default_search(session, lead_project)
    session.add(Article(project_id=lead_project.id, pmid="1", title="Mice"))
    await session.commit()
    assert (await client.get(_pdfs_url(lead_project))).status_code == 404


@pytest.mark.asyncio
async def test_export_pdfs_forbidden_for_non_member(client, session, do_login, researcher_user):
    project = Project(title="P", created_by=researcher_user.id, question="Q", fields=LEGACY_FIELDS)
    session.add(project)
    await session.commit()
    await ensure_default_search(session, project)
    await _make_user(session, "out@lab.fr", UserRole.RESEARCHER, "out-pass-123")
    await do_login("out@lab.fr", "out-pass-123", consent=True)
    assert (await client.get(_pdfs_url(project))).status_code == 403


@pytest.mark.asyncio
async def test_export_pdfs_scoped_to_search_lot(client, session, lead_project):
    await ensure_default_search(session, lead_project)
    inside = Article(project_id=lead_project.id, pmid="1", title="In lot")
    outside = Article(project_id=lead_project.id, pmid="2", title="Out lot")
    session.add_all([inside, outside])
    await session.flush()
    session.add_all(
        [
            ArticleFile(article_id=inside.id, data=b"%PDF-1.4 in"),
            ArticleFile(article_id=outside.id, data=b"%PDF-1.4 out"),
        ]
    )
    await session.commit()
    lot = Search(project_id=lead_project.id, name="lot", lot_all=False)
    session.add(lot)
    await session.flush()
    session.add(SearchArticle(search_id=lot.id, article_id=inside.id))
    await session.commit()

    r = await client.get(_pdfs_url(lead_project), params={"search": str(lot.id)})
    assert r.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["In lot.pdf"]
    # recherche inconnue (ou d'un autre projet) : 404, comme les autres exports
    assert (
        await client.get(_pdfs_url(lead_project), params={"search": str(uuid.uuid4())})
    ).status_code == 404
