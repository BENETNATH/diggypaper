"""Full text outside PMC: PDF → sections, Europe PMC / Unpaywall / OpenAlex chain (offline)."""

from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select

from app.article_text import compose_text, fill_prompt
from app.db.models import Article, ArticleFile
from app.fulltext import apply_uploaded_pdf, complete_article, find_open_fulltext, pdf_to_sections

BODY = "lorem ipsum dolor sit amet " * 100


def make_pdf(lines: list[str]) -> bytes:
    """PDF 1 page minimal (police Helvetica) contenant ``lines``."""
    content = "BT /F1 10 Tf 12 TL 40 760 Td " + " ".join(f"({x}) Tj T*" for x in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_pdf_to_sections_splits_imrad_and_stops_at_references():
    pdf = make_pdf(
        [
            "Title of paper",
            "Abstract",
            "short abstract",
            "1. Introduction",
            "why we did it",
            "Materials and Methods",
            "mice were housed",
            "Results",
            "they ran",
            "References",
            "[1] x",
        ]
    )
    secs = pdf_to_sections(pdf)
    assert [s["kind"] for s in secs] == ["other", "other", "introduction", "methods", "results"]
    assert "mice were housed" in secs[3]["text"]
    assert all("[1] x" not in s["text"] for s in secs)


async def test_find_open_fulltext_falls_back_to_preprint_pdf():
    pdf = make_pdf(["Methods"] + [BODY[i : i + 80] for i in range(0, 2400, 80)])

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "europepmc" in url and "search" in url:
            return httpx.Response(200, json={"resultList": {"result": [{"doi": "10.1/x"}]}})
        if "unpaywall" in url:
            return httpx.Response(
                200,
                json={
                    "oa_locations": [
                        {"url_for_pdf": "https://repo.org/a.pdf", "version": "submittedVersion"}
                    ]
                },
            )
        if "openalex" in url:
            return httpx.Response(200, json={"locations": []})
        if url == "https://repo.org/a.pdf":
            return httpx.Response(200, content=pdf)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        secs, source, url, pdf_bytes = await find_open_fulltext("123", None, None, "a@b.fr", http)
    assert source == "pdf:preprint" and url == "https://repo.org/a.pdf"
    assert pdf_bytes == pdf
    assert secs and secs[0]["kind"] == "methods"


async def test_find_open_fulltext_nothing_found():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))
    ) as http:
        assert await find_open_fulltext("1", None, "10.1/x", "a@b.fr", http) == (
            [],
            None,
            None,
            None,
        )


async def test_upload_pdf_sets_fulltext(client, session, researcher_user, do_login):
    import uuid

    from app.db.models import Article, Project, ProjectMember, ProjectMemberRole

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    art = Article(project_id=project.id, pmid="1", title="T", abstract="A")
    session.add(art)
    await session.commit()
    pdf = make_pdf(["Methods"] + [BODY[i : i + 80] for i in range(0, 2400, 80)])
    url = f"/api/v1/projects/{project.id}/articles/{art.id}/fulltext"
    resp = await client.post(url, content=pdf, headers={"content-type": "application/pdf"})
    assert resp.status_code == 200
    assert resp.json()["has_fulltext"] and resp.json()["fulltext_source"] == "pdf:upload"
    bad = await client.post(url, content=b"not a pdf", headers={"content-type": "application/pdf"})
    assert bad.status_code == 422
    assert uuid.UUID(resp.json()["id"]) == art.id


def test_pdf_to_sections_strips_nul_chars(monkeypatch):
    """NUL → Postgres JSONB refuses ("unsupported Unicode escape sequence")."""
    import pypdf

    class Page:
        def extract_text(self):
            return "Methods\nmice\x00 were housed"

    class Reader:
        def __init__(self, _):
            self.pages = [Page()]

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    secs = pdf_to_sections(b"%PDF")
    assert "\x00" not in secs[0]["text"] and "mice were housed" in secs[0]["text"]


@pytest.fixture(autouse=True)
def _public_hosts(monkeypatch):
    """No DNS in tests: the fictitious hosts are "public"."""
    monkeypatch.setattr("app.fulltext.check_public_url", lambda url: None)


async def test_download_pdf_refuses_redirect_to_internal_host(monkeypatch):
    from app.fulltext import _download_pdf

    def check(url):
        if "internal" in url:
            raise ValueError("not public")

    monkeypatch.setattr("app.fulltext.check_public_url", check)
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        if request.url.host == "open.example":
            return httpx.Response(302, headers={"location": "http://internal.local/secret"})
        return httpx.Response(200, content=b"%PDF-1.4 secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await _download_pdf("https://open.example/a.pdf", http) is None
    assert hits == ["open.example"]  # the internal skip was never requested again


# --- is the PDF really this article? ---------------------------------------------------

TITLE = "An automated machine vision-based index for Rett Syndrome in mouse models"


def _sec(text):
    return [{"title": "", "kind": "other", "text": text}]


def test_pdf_matches_article_title_doi_and_unverifiable():
    from app.fulltext import pdf_matches_article as match

    ok = _sec("Berger. An Automated machine-vision based INDEX for Rett syndrome in mouse models. ")
    assert match(TITLE, None, ok) is True  # case, dashes, punctuation ignored
    other = _sec("Deep learning for protein folding prediction across large genomes " * 5)
    assert match(TITLE, None, other) is False
    # badly extracted title, but the article's DOI at the top of the document
    garbled = _sec("doi: 10.64898/2026.09.21.753377 xx qq rr")
    assert match(TITLE, "10.64898/2026.09.21.753377", garbled) is True
    assert match("Short", None, other) is None and match(None, None, other) is None


async def test_find_open_fulltext_skips_pdf_of_another_article():
    wrong = make_pdf(
        ["Cancer genomics of rare tumours"] + [BODY[i : i + 80] for i in range(0, 2400, 80)]
    )
    right = make_pdf([TITLE] + [BODY[i : i + 80] for i in range(0, 2400, 80)])

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "unpaywall" in url:
            return httpx.Response(
                200,
                json={
                    "oa_locations": [
                        {"url_for_pdf": "https://a.org/wrong.pdf", "version": "publishedVersion"},
                        {"url_for_pdf": "https://b.org/right.pdf", "version": "submittedVersion"},
                    ]
                },
            )
        if "openalex" in url:
            return httpx.Response(200, json={"locations": []})
        return {
            "https://a.org/wrong.pdf": httpx.Response(200, content=wrong),
            "https://b.org/right.pdf": httpx.Response(200, content=right),
        }.get(url, httpx.Response(404))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        _, source, url, _ = await find_open_fulltext("1", None, "10.1/x", "a@b.fr", http, TITLE)
    assert url == "https://b.org/right.pdf" and source == "pdf:preprint"


async def _lead_article(session, user, **art):
    from app.db.models import Article, Project, ProjectMember, ProjectMemberRole

    project = Project(title="P", created_by=user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    a = Article(project_id=project.id, pmid="1", **art)
    session.add(a)
    await session.commit()
    return project, a


@pytest.fixture
async def article_with_pdf(session):
    """Article already holding a source (preprint PDF, methods, note) to be replaced."""
    art = Article(
        pmid="123",
        title=TITLE,
        abstract="Current abstract",
        sections=[{"title": "Methods", "kind": "methods", "text": "Prior methods"}],
        methods_text="Prior methods",
        fulltext_source="pdf:preprint",
        fulltext_url="https://repo.org/prior.pdf",
        fulltext_note="prior note",
    )
    session.add(art)
    await session.flush()
    session.add(ArticleFile(article_id=art.id, data=b"%PDF prior"))
    await session.commit()
    return art


def _source_state(art):
    return (
        art.sections,
        art.fulltext_source,
        art.fulltext_url,
        art.fulltext_note,
        art.methods_text,
    )


async def _pdf_data(session, article_id):
    rows = await session.scalars(
        select(ArticleFile.data).where(ArticleFile.article_id == article_id)
    )
    return list(rows)


@pytest.mark.parametrize("pdf", [None, b"%PDF replacement"], ids=["jats", "pdf"])
@pytest.mark.parametrize("has_methods", [True, False], ids=["methods", "no-methods"])
async def test_complete_article_replaces_entire_source(
    session, article_with_pdf, monkeypatch, pdf, has_methods
):
    """The new source replaces everything: the old PDF does not survive a JATS text, and the
    old source's methods are not inherited by a text that has none."""
    art = article_with_pdf
    sections = [{"title": "Results", "kind": "results", "text": BODY}]
    if has_methods:
        sections += [
            {"title": "Methods", "kind": "methods", "text": "New methods"},
            {"title": "More methods", "kind": "methods", "text": "Additional methods"},
        ]
    source = "europepmc" if pdf is None else "pdf:published"
    url = "https://source.org/current"
    monkeypatch.setattr(
        "app.fulltext.find_open_fulltext", AsyncMock(return_value=(sections, source, url, pdf))
    )

    async with httpx.AsyncClient() as http:
        assert await complete_article(session, art, "a@b.fr", http) == source
    await session.commit()
    await session.refresh(art)

    methods = "New methods Additional methods" if has_methods else None
    assert _source_state(art) == (sections, source, url, None, methods)
    assert await _pdf_data(session, art.id) == ([] if pdf is None else [pdf])
    assert "Prior methods" not in compose_text(art)
    assert "Prior methods" not in fill_prompt("{article_text}|{methods_text}", art)
    if not has_methods:
        assert compose_text(art) == "Current abstract"
        assert fill_prompt("{methods_text}", art) == ""


async def test_uploaded_pdf_replaces_source_without_old_methods(session, article_with_pdf):
    art = article_with_pdf
    sections = [{"title": "Results", "kind": "results", "text": BODY}]
    await apply_uploaded_pdf(session, art, sections, b"%PDF uploaded")
    await session.commit()
    await session.refresh(art)
    assert _source_state(art) == (sections, "pdf:upload", None, None, None)
    assert await _pdf_data(session, art.id) == [b"%PDF uploaded"]


@pytest.mark.parametrize("pdf", [None, b"%PDF replacement"], ids=["jats", "pdf"])
async def test_source_replacement_failure_rolls_back_even_if_caller_commits(
    session, article_with_pdf, monkeypatch, pdf
):
    """An error during the replacement leaves the old source intact (text AND PDF), even if the
    caller intercepts the error and then commits its progress."""
    art = article_with_pdf
    original = _source_state(art)
    source = "europepmc" if pdf is None else "pdf:published"
    # The PubMed notice can be refreshed independently of the full text.
    art.title = "Updated bibliographic title"
    monkeypatch.setattr(
        "app.fulltext.find_open_fulltext",
        AsyncMock(return_value=(_sec(BODY), source, "https://source.org/current", pdf)),
    )

    def fail_after_write(*_):
        if art.fulltext_source == source:
            raise RuntimeError("source write failed")

    event.listen(session.sync_session, "after_flush_postexec", fail_after_write)
    try:
        async with httpx.AsyncClient() as http:
            with pytest.raises(RuntimeError, match="source write failed"):
                await complete_article(session, art, "a@b.fr", http)
    finally:
        event.remove(session.sync_session, "after_flush_postexec", fail_after_write)
    # Le traitement en lot intercepte les erreurs, puis committe sa progression.
    await session.commit()
    await session.refresh(art)
    assert art.title == "Updated bibliographic title"
    assert _source_state(art) == original
    assert await _pdf_data(session, art.id) == [b"%PDF prior"]


async def test_batch_fulltext_continues_after_source_write_failure(
    engine, session, article_with_pdf, monkeypatch, caplog
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.models import ImportJob, ImportJobStatus, Project
    from app.tasks import _complete_fulltext

    project = Project(title="Batch completion")
    session.add(project)
    await session.flush()
    art = article_with_pdf
    art.project_id = project.id
    second = Article(project_id=project.id, pmid="124", title=TITLE)
    session.add(second)
    await session.commit()
    original = _source_state(art)
    monkeypatch.setattr("app.tasks.backfill_metadata", AsyncMock(return_value=0))
    monkeypatch.setattr(
        "app.fulltext.find_open_fulltext",
        AsyncMock(return_value=(_sec(BODY), "europepmc", "https://source.org/current", None)),
    )

    def fail_first_write(_mapper, _connection, target):
        if target.pmid == "123":
            raise RuntimeError("first source write failed")

    event.listen(Article, "after_update", fail_first_write)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient() as http:
            result = await _complete_fulltext(str(project.id), session_factory=factory, http=http)
    finally:
        event.remove(Article, "after_update", fail_first_write)
    assert result == {
        "phase": "done",
        "done": 2,
        "total": 2,
        "found": 1,
        "sources": {"europepmc": 1},
    }
    assert "open fulltext failed for pmid 123" in caplog.text
    assert "first source write failed" in caplog.text
    await session.refresh(art)
    await session.refresh(second)
    assert _source_state(art) == original
    assert await _pdf_data(session, art.id) == [b"%PDF prior"]
    assert second.fulltext_source == "europepmc"
    job = await session.scalar(select(ImportJob).where(ImportJob.project_id == project.id))
    assert job.status == ImportJobStatus.COMPLETED


async def test_upload_pdf_of_another_article_needs_force(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, art = await _lead_article(session, researcher_user, title=TITLE, abstract="A")
    wrong = make_pdf(
        ["Cancer genomics of rare tumours"] + [BODY[i : i + 80] for i in range(0, 2400, 80)]
    )
    right = make_pdf([TITLE] + [BODY[i : i + 80] for i in range(0, 2400, 80)])
    url = f"/api/v1/projects/{project.id}/articles/{art.id}/fulltext"
    hdr = {"content-type": "application/pdf"}
    r = await client.post(url, content=wrong, headers=hdr)
    assert r.status_code == 422 and r.json()["detail"] == "pdf_mismatch"
    r = await client.post(url + "?force=true", content=wrong, headers=hdr)
    assert r.status_code == 200 and r.json()["pdf_check"] == "forced"
    r = await client.post(url, content=right, headers=hdr)
    assert r.status_code == 200 and r.json()["pdf_check"] == "match"


async def test_search_fulltext_relaunch_for_one_article(
    client, session, researcher_user, do_login, monkeypatch
):
    calls = []

    async def fake_refresh(db, art, http):
        calls.append("refresh")
        art.pmcid = "PMC42"  # the preprint was published in the meantime

    async def fake_find(pmid, pmcid, doi, email, http, title=None):
        calls.append((pmcid, title))
        secs = [{"title": "Methods", "kind": "methods", "text": "mice " * 400}]
        return secs, "europepmc", "https://europepmc.org/article/PMC/PMC42", None

    monkeypatch.setattr("app.fulltext.refresh_article_metadata", fake_refresh)
    monkeypatch.setattr("app.fulltext.find_open_fulltext", fake_find)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, art = await _lead_article(session, researcher_user, title=TITLE, abstract="A")
    url = f"/api/v1/projects/{project.id}/articles/{art.id}/fulltext/search"
    r = await client.post(url)
    assert r.status_code == 200 and r.json()["found"] and r.json()["source"] == "europepmc"
    assert r.json()["article"]["has_fulltext"] and r.json()["article"]["pmcid"] == "PMC42"
    assert calls == ["refresh", ("PMC42", TITLE)]
    # a manually uploaded PDF is not overwritten by the automatic search
    art.fulltext_source = "pdf:upload"
    await session.commit()
    r = await client.post(url)
    assert r.json()["found"] is False and len(calls) == 3  # refresh seulement


async def test_bulk_pdf_matches_the_right_article(client, session, researcher_user, do_login):
    from app.db.models import Article

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, a1 = await _lead_article(session, researcher_user, title=TITLE, doi="10.1/aa")
    a2 = Article(
        project_id=project.id, pmid="2", title="Cancer genomics of rare tumours", doi="10.1/bb"
    )
    a3 = Article(project_id=project.id, pmid="3", title="Cancer genomics of rare tumours in adults")
    session.add_all([a2, a3])
    await session.commit()
    url = f"/api/v1/projects/{project.id}/fulltext/match"
    hdr = {"content-type": "application/pdf"}
    body = [BODY[i : i + 80] for i in range(0, 2400, 80)]

    r = await client.post(url, content=make_pdf([TITLE] + body), headers=hdr)
    assert r.status_code == 200 and r.json()["article_id"] == str(a1.id)
    await session.refresh(a1)
    assert a1.fulltext_source == "pdf:upload" and a1.sections
    # DOI at the top: breaks the tie between two nearly identical titles
    r = await client.post(
        url,
        content=make_pdf(["Cancer genomics of rare tumours in adults", "doi 10.1/aa"] + body),
        headers=hdr,
    )
    assert r.json()["article_id"] == str(a1.id)  # le DOI prime sur le titre
    # two equally plausible articles: no choice made for the user
    r = await client.post(
        url, content=make_pdf(["Cancer genomics of rare tumours in adults"] + body), headers=hdr
    )
    assert r.status_code == 409 and r.json()["detail"] == "ambiguous"
    # aucun article ne ressemble
    r = await client.post(
        url, content=make_pdf(["Unrelated quantum chromodynamics study"] + body), headers=hdr
    )
    assert r.status_code == 404 and r.json()["detail"] == "no_match"


def test_doi_must_match_exactly_not_as_a_prefix():
    from app.fulltext import doi_in

    head = "Cell. doi: 10.1016/j.cell.2020.12.003 Published online."
    assert doi_in(head, "10.1016/j.cell.2020.12.003")
    assert doi_in(head, "10.1016/J.CELL.2020.12.003")  # case ignored
    assert not doi_in(head, "10.1016/j.cell.2020.1")  # prefix of another DOI
    assert not doi_in(head, "10.1016/j.cell.2020.12")
    assert doi_in("see 10.1016/j.cell.2020.12.003.", "10.1016/j.cell.2020.12.003")  # point final
    assert doi_in(
        "doi 10.1016/j.cell.2020.12.\n003", "10.1016/j.cell.2020.12.003"
    )  # cut across lines
    assert not doi_in(head, None)
