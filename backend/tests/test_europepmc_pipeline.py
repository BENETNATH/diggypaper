"""Phase 3 multi-sources: Europe PMC pipeline + full text by ``fulltext_id``
(spec 2026-10-29-sources-phase3 D2) — 100% offline (fake adapter,
``test_sources`` conventions)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_sources import FakeSourceAdapter

from app.db.models import Article, ImportJob, Project
from app.tasks import _import_pubmed

OK = [{"title": "Methods", "kind": "methods", "text": "Outcome measured. " * 120}]


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_project(engine) -> Project:
    async with _factory(engine)() as session:
        project = Project(title="EPMC")
        session.add(project)
        await session.flush()
        await session.commit()
        return project


async def _articles(engine, project_id) -> list[Article]:
    async with _factory(engine)() as session:
        return list(
            (await session.execute(select(Article).where(Article.project_id == project_id)))
            .scalars()
            .all()
        )


async def _jobs(engine, project_id) -> list[ImportJob]:
    async with _factory(engine)() as session:
        return list(
            (await session.execute(select(ImportJob).where(ImportJob.project_id == project_id)))
            .scalars()
            .all()
        )


async def test_europepmc_import_sets_identity_and_fulltext(engine):
    """``europepmc`` import: source/native_id set, ``pmid`` when the record carries one,
    JATS full text via ``fulltext_id`` (label + record URL)."""
    project = await _make_project(engine)
    fake = FakeSourceAdapter(
        volume=2,
        ids=["W1", "W2"],
        meta_by_id={
            "W1": {
                "native_id": "W1",
                "pmid": "34335492",
                "doi": "10.1000/xyz.1",
                "title": "T1",
                "fulltext_id": "PMC99999",
                "fulltext_url": "https://europepmc.org/article/MED/34335492",
                "fulltext_source": "europepmc",
            },
            "W2": {"native_id": "W2", "title": "T2"},
        },
        fulltext_by_id={"PMC99999": ("methods text", OK)},
    )
    result = await _import_pubmed(
        str(project.id), "q", session_factory=_factory(engine), adapter=fake, source="europepmc"
    )
    assert result["imported"] == 2 and result["failed"] == 0 and result["fulltext"] == 1
    arts = await _articles(engine, project.id)
    by_nid = {a.native_id: a for a in arts}
    w1 = by_nid["W1"]
    assert w1.source == "europepmc"
    assert w1.pmid == "34335492"
    assert w1.sections and w1.methods_text == "methods text"
    assert w1.fulltext_source == "europepmc"
    assert w1.fulltext_url == "https://europepmc.org/article/MED/34335492"
    w2 = by_nid["W2"]
    assert (w2.source, w2.pmid) == ("europepmc", None)
    # full text is requested ONLY for the OA record, by its fulltext_id
    assert [c[1] for c in fake.calls if c[0] == "fetch_fulltext"] == ["PMC99999"]
    job = (await _jobs(engine, project.id))[0]
    assert job.source == "europepmc"


async def test_europepmc_non_oa_no_fulltext_call(engine):
    """Record without ``fulltext_id`` (non-OA): no full-text call, no note."""
    project = await _make_project(engine)
    fake = FakeSourceAdapter(
        volume=1,
        ids=["W9"],
        meta_by_id={"W9": {"native_id": "W9", "title": "T9"}},
    )
    result = await _import_pubmed(
        str(project.id), "q", session_factory=_factory(engine), adapter=fake, source="europepmc"
    )
    assert result["imported"] == 1 and result["fulltext"] == 0
    art = (await _articles(engine, project.id))[0]
    assert not art.sections and art.fulltext_note is None and art.fulltext_source is None
    assert not any(c[0] == "fetch_fulltext" for c in fake.calls)
