"""Phase 4b : pipeline HAL (spec 2026-10-30-sources-phase4b D1–D3) — 100 % offline
(fake adapter, pattern 4a)."""

from test_source_dedup import _articles, _factory, _make_project
from test_sources import FakeSourceAdapter

from app.db.models import Project, ProjectStatus
from app.tasks import _import_pubmed

FILE_URL = "https://hal.science/hal-00478278/document"
SECTIONS = [{"title": "Methods", "kind": "methods", "text": "Cohort of 200 patients. " * 40}]

META = {
    "hal-00478278": {
        "native_id": "hal-00478278",
        "title": "T1",
        "doi": "10.1007/s10549-008-0235-7",
        "fulltext_id": FILE_URL,
        "fulltext_source": "hal",
        "fulltext_url": FILE_URL,
    },
    "hal-04167683": {
        "native_id": "hal-04167683",
        "title": "T2",
        "fulltext_source": "hal",
        "fulltext_note": "hal_no_file",
    },
}


def _fake() -> FakeSourceAdapter:
    return FakeSourceAdapter(
        volume=2,
        ids=["hal-00478278", "hal-04167683"],
        meta_by_id=META,
        fulltext_by_id={FILE_URL: (None, SECTIONS)},
    )


async def _import(engine, project, fake) -> dict:
    return await _import_pubmed(
        str(project.id), "q", session_factory=_factory(engine), adapter=fake, source="hal"
    )


async def _status(engine, project_id) -> ProjectStatus:
    async with _factory(engine)() as session:
        return (await session.get(Project, project_id)).status


async def test_hal_import_full_flow(engine):
    """``hal`` import: source/native_id set, PDF full text set
    (sections + fulltext_source/url) for the record with file, explicit note
    for the one without; fetch_fulltext called for the only known URL."""
    project = await _make_project(engine)
    fake = _fake()
    result = await _import(engine, project, fake)
    assert result["imported"] == 2 and result["failed"] == 0 and result["fulltext"] == 1
    arts = await _articles(engine, project.id)
    by_nid = {a.native_id: a for a in arts}
    assert set(by_nid) == {"hal-00478278", "hal-04167683"}
    for nid in by_nid:
        assert by_nid[nid].source == "hal"
    a = by_nid["hal-00478278"]
    assert a.sections == SECTIONS and a.methods_text is None
    assert a.fulltext_source == "hal" and a.fulltext_url == FILE_URL
    assert a.doi == "10.1007/s10549-008-0235-7" and a.title == "T1"
    b = by_nid["hal-04167683"]
    assert b.fulltext_note == "hal_no_file"
    assert (b.sections is None or not b.sections) and b.fulltext_url is None
    ft_calls = [c for c in fake.calls if c[0] == "fetch_fulltext"]
    assert ft_calls == [("fetch_fulltext", FILE_URL)]
    assert await _status(engine, project.id) == ProjectStatus.FROZEN


async def test_hal_reimport_same_identity_noop(engine):
    """Re-import same identity: 0 inserted (ON CONFLICT), the note is not overwritten."""
    project = await _make_project(engine)
    await _import(engine, project, _fake())
    result = await _import(engine, project, _fake())
    assert result["imported"] == 0
    a = {x.native_id: x for x in await _articles(engine, project.id)}
    assert a["hal-04167683"].fulltext_note == "hal_no_file"


async def test_hal_doi_link_completes_existing_row(engine):
    """HAL Work carrying the DOI of an existing fulltext-less row: attachment
    — sections/fulltext_source completed, identity intact, ``duplicates_doi``."""
    project = await _make_project(
        engine,
        {
            "source": "crossref",
            "native_id": "10.1007/s10549-008-0235-7",
            "doi": "10.1007/s10549-008-0235-7",
            "title": "T1",
        },
    )
    result = await _import(engine, project, _fake())
    assert result["imported"] == 1 and result.get("duplicates_doi") == 1
    arts = await _articles(engine, project.id)
    by_nid = {a.native_id: a for a in arts}
    a = by_nid["10.1007/s10549-008-0235-7"]
    assert (a.source, a.native_id) == ("crossref", "10.1007/s10549-008-0235-7")  # identity intact
    assert a.sections == SECTIONS and a.fulltext_source == "hal"
    assert by_nid["hal-04167683"].fulltext_note == "hal_no_file"  # the other inserted
