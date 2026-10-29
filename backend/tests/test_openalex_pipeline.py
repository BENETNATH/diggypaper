"""Phase 4a : pipeline OpenAlex — source métadonnées seulement
(spec 2026-10-29-sources-phase4a D1/D2) — 100 % offline (fake adapter)."""

from test_source_dedup import _articles, _factory, _make_project
from test_sources import FakeSourceAdapter

from app.db.models import Project, ProjectStatus
from app.tasks import _import_pubmed

META = {
    "W1": {
        "native_id": "W1",
        "title": "T1",
        "doi": "10.1000/oa.1",
        "fulltext_note": "metadata_only",
    },
    "W2": {
        "native_id": "W2",
        "title": "T2",
        "fulltext_note": "metadata_only",
    },
}


def _fake() -> FakeSourceAdapter:
    return FakeSourceAdapter(volume=2, ids=["W1", "W2"], meta_by_id=META)


async def _import(engine, project, fake) -> dict:
    return await _import_pubmed(
        str(project.id), "q", session_factory=_factory(engine), adapter=fake, source="openalex"
    )


async def test_openalex_import_metadata_only(engine):
    """Import ``openalex`` : source/native_id posés, ``fulltext_note="metadata_only"``
    explicite, aucun appel plein texte, ``fulltext == 0``."""
    project = await _make_project(engine)
    fake = _fake()
    result = await _import(engine, project, fake)
    assert result["imported"] == 2 and result["failed"] == 0 and result["fulltext"] == 0
    arts = await _articles(engine, project.id)
    by_nid = {a.native_id: a for a in arts}
    assert by_nid["W1"].source == "openalex" and by_nid["W1"].title == "T1"
    for nid in ("W1", "W2"):
        a = by_nid[nid]
        assert a.fulltext_note == "metadata_only"
        assert a.sections is None or not a.sections
        assert a.methods_text is None and a.fulltext_url is None
    assert "fetch_fulltext" not in [c[0] for c in fake.calls]
    assert await _status(engine, project.id) == ProjectStatus.FROZEN


async def test_openalex_reimport_same_identity_noop(engine):
    """Réimport même identité : 0 inséré (ON CONFLICT), la note n'est pas écrasée."""
    project = await _make_project(engine)
    fake = _fake()
    await _import(engine, project, fake)
    result = await _import(engine, project, _fake())
    assert result["imported"] == 0
    a = await _articles(engine, project.id)
    assert [x.fulltext_note for x in a] == ["metadata_only", "metadata_only"]


async def test_openalex_doi_link_sets_note_on_existing_row(engine):
    """Work OpenAlex portant le DOI d'une ligne existante sans fulltext : rattachement
    — ``fulltext_note`` posé (si vide), identité et origine intactes, ``duplicates_doi`` ;
    le second work (sans DOI) est inséré normalement."""
    project = await _make_project(
        engine,
        {"source": "crossref", "native_id": "10.1000/oa.1", "doi": "10.1000/oa.1", "title": "T1"},
    )
    result = await _import(engine, project, _fake())
    assert result["imported"] == 1 and result.get("duplicates_doi") == 1  # W1 lié, W2 inséré
    arts = await _articles(engine, project.id)
    by_nid = {a.native_id: a for a in arts}
    assert set(by_nid) == {"10.1000/oa.1", "W2"}
    a = by_nid["10.1000/oa.1"]
    assert (a.source, a.native_id) == ("crossref", "10.1000/oa.1")  # identité intacte
    assert a.fulltext_note == "metadata_only"
    assert by_nid["W2"].source == "openalex" and by_nid["W2"].fulltext_note == "metadata_only"


async def _status(engine, project_id) -> ProjectStatus:
    async with _factory(engine)() as session:
        return (await session.get(Project, project_id)).status
