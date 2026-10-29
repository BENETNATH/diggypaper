"""Phase 3 multi-sources : rattachement DOI avec complétion des champs manquants
(spec 2026-10-29-sources-phase3 D3, archi § 3) — 100 % offline."""

from test_source_dedup import _articles, _factory, _make_project
from test_sources import FakeSourceAdapter

from app.db.models import Project, ProjectStatus
from app.tasks import _import_pubmed

OK = [{"title": "Methods", "kind": "methods", "text": "Outcome measured. " * 120}]

NOTICE = {
    "native_id": "W123",
    "doi": "10.1000/xyz.1",
    "title": "New title",
    "abstract": "New abstract.",
    "journal": "Journal X",
    "pub_date": "2024-05-01",
    "language": "eng",
    "pmcid": "PMC12345",
    "fulltext_id": "PMC12345",
    "fulltext_url": "https://europepmc.org/article/MED/34335492",
    "fulltext_source": "europepmc",
}


def _fake(notice: dict, fulltext=True) -> FakeSourceAdapter:
    return FakeSourceAdapter(
        volume=1,
        ids=["W123"],
        meta_by_id={"W123": notice},
        fulltext_by_id={"PMC12345": ("methods text", OK)} if fulltext else {},
    )


async def _import_epmc(engine, project, fake) -> dict:
    return await _import_pubmed(
        str(project.id), "q", session_factory=_factory(engine), adapter=fake, source="europepmc"
    )


async def _redraft(engine, project_id) -> None:
    async with _factory(engine)() as session:
        (await session.get(Project, project_id)).status = ProjectStatus.DRAFT
        await session.commit()


async def test_doi_duplicate_other_identity_links_and_fills(engine):
    """Ligne ``pubmed`` existante (même DOI, champs vides) : la notice ``europepmc`` la
    complète — champs vides remplis, sections posées, identité et origine intactes,
    compté ``duplicates_doi``, aucune nouvelle ligne."""
    project = await _make_project(
        engine,
        {"source": "pubmed", "native_id": "34335492", "pmid": "34335492", "doi": "10.1000/xyz.1"},
    )
    result = await _import_epmc(engine, project, _fake(NOTICE))
    assert result["imported"] == 0 and result.get("duplicates_doi") == 1
    arts = await _articles(engine, project.id)
    assert len(arts) == 1
    a = arts[0]
    assert (a.source, a.native_id, a.pmid) == ("pubmed", "34335492", "34335492")  # identité
    assert a.origin is None  # non touchée
    assert a.abstract == "New abstract."  # champ vide complété
    assert a.journal == "Journal X"
    assert a.pmcid == "PMC12345"
    assert a.sections and a.fulltext_source == "europepmc"
    assert a.fulltext_url == "https://europepmc.org/article/MED/34335492"


async def test_link_never_overwrites_existing_values(engine):
    """Une valeur existante non vide n'est jamais écrasée (pas de fusion de valeurs)."""
    project = await _make_project(
        engine,
        {
            "source": "pubmed",
            "native_id": "34335492",
            "pmid": "34335492",
            "doi": "10.1000/xyz.1",
            "title": "Old title",
            "abstract": "Old abstract.",
            "journal": "Old Journal",
            "pub_date": "2020-01-01",
        },
    )
    await _import_epmc(engine, project, _fake(NOTICE))
    a = (await _articles(engine, project.id))[0]
    assert a.title == "Old title"
    assert a.abstract == "Old abstract."
    assert a.journal == "Old Journal"
    assert a.pub_date == "2020-01-01"


async def test_link_skips_fulltext_when_row_has_sections(engine):
    """Ligne existante déjà pourvue de sections : aucun appel plein texte."""
    project = await _make_project(
        engine,
        {
            "source": "pubmed",
            "native_id": "34335492",
            "pmid": "34335492",
            "doi": "10.1000/xyz.1",
            "sections": OK,
        },
    )
    fake = _fake(NOTICE)
    await _import_epmc(engine, project, fake)
    assert not any(c[0] == "fetch_fulltext" for c in fake.calls)
    a = (await _articles(engine, project.id))[0]
    assert a.fulltext_source is None  # le rattachement ne touche pas le texte existant


async def test_link_prefers_smallest_article_id(engine):
    """Deux lignes porteuses du DOI : la première en ``Article.id`` est complétée, pas l'autre."""
    project = await _make_project(
        engine,
        {"source": "pubmed", "native_id": "1", "pmid": "1", "doi": "10.1000/two.1"},
        {"source": "europepmc", "native_id": "W9", "doi": "10.1000/two.1"},
    )
    notice = dict(NOTICE, doi="10.1000/two.1", pmcid=None, fulltext_id=None, fulltext_url=None)
    await _import_epmc(engine, project, _fake(notice, fulltext=False))
    arts = await _articles(engine, project.id)
    first = min(arts, key=lambda a: a.id)
    other = max(arts, key=lambda a: a.id)
    assert first.journal == "Journal X"
    assert other.journal is None


async def test_reimport_same_identity_not_linked(engine):
    """Garde : ré-import de la même identité → ``ON CONFLICT`` (ni rattachement, ni
    ``duplicates_doi``, ni plein texte)."""
    project = await _make_project(engine)
    fake = _fake(NOTICE)
    await _import_epmc(engine, project, fake)
    await _redraft(engine, project.id)
    again = FakeSourceAdapter(
        volume=1, ids=["W123"], meta_by_id={"W123": dict(NOTICE, abstract="Autre résumé")}
    )
    result = await _import_epmc(engine, project, again)
    assert result["imported"] == 0 and result.get("duplicates_doi", 0) == 0
    assert len(await _articles(engine, project.id)) == 1
    assert (await _articles(engine, project.id))[0].abstract == "New abstract."  # pas de fusion


async def test_link_no_doi_plain_insert(engine):
    """Notice sans DOI : insertion normale, ``duplicates_doi`` absent du payload."""
    project = await _make_project(engine)
    notice = {k: v for k, v in NOTICE.items() if k != "doi"}
    result = await _import_epmc(engine, project, _fake(notice))
    assert result["imported"] == 1 and "duplicates_doi" not in result
    assert len(await _articles(engine, project.id)) == 1


async def test_doi_duplicate_same_import_still_skipped_not_linked(engine):
    """Deux notices du même import portant le même DOI : la 2ᵉ est sautée (phase 2) — pas
    rattachée, son plein texte n'est pas demandé, ses champs ne sont pas fusionnés."""
    project = await _make_project(engine)
    fake = FakeSourceAdapter(
        volume=2,
        ids=["W1", "W2"],
        meta_by_id={
            "W1": {"native_id": "W1", "doi": "10.1000/same.1", "title": "T1"},
            "W2": {
                "native_id": "W2",
                "doi": "10.1000/same.1",
                "title": "T2",
                "abstract": "From W2",
            },
        },
    )
    result = await _import_epmc(engine, project, fake)
    assert result["imported"] == 1 and result.get("duplicates_doi") == 1
    assert not any(c[0] == "fetch_fulltext" for c in fake.calls)
    w1 = next(a for a in await _articles(engine, project.id) if a.native_id == "W1")
    assert w1.abstract is None  # pas de fusion de la notice W2
