"""Phase 2 multi-sources : identité ``(project_id, source, native_id)`` + migration 0030
(spec 2026-10-29-sources-phase2-identite §1-§2) — 100 % offline."""

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, ImportJob, ImportJobStatus, Project, ProjectStatus
from app.refimport import parse_references
from app.sources import DateBounds
from app.tasks import _import_pubmed, import_pubmed_task

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Pas de vrai sleep (throttle NCBI + backoff)."""

    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_project(engine) -> Project:
    async with _factory(engine)() as session:
        project = Project(title="Sources")
        session.add(project)
        await session.commit()
        return project


async def _rows(engine, model, project_id) -> list:
    async with _factory(engine)() as session:
        return list(
            (await session.execute(select(model).where(model.project_id == project_id)))
            .scalars()
            .all()
        )


def _efetch_xml(items: list[tuple[str, str | None]]) -> str:
    """efetch PubMed : un PubmedArticle par (pmid, doi) — sans PMCID (pas de plein texte)."""
    out = []
    for pmid, doi in items:
        doi_tag = f'<ArticleId IdType="doi">{doi}</ArticleId>' if doi else ""
        out.append(
            f"""<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>
<ArticleTitle>Title {pmid}</ArticleTitle><Abstract><AbstractText>A.</AbstractText></Abstract>
<Language>English</Language></Article></MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="pubmed">{pmid}</ArticleId>{doi_tag}</ArticleIdList></PubmedData>
</PubmedArticle>"""
        )
    return f"<PubmedArticleSet>{''.join(out)}</PubmedArticleSet>"


def _ncbi(items: list[tuple[str, str | None]]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            if request.url.params.get("retmax") == "0":
                return httpx.Response(200, json={"esearchresult": {"count": str(len(items))}})
            return httpx.Response(200, json={"esearchresult": {"idlist": [p for p, _ in items]}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(200, text=_efetch_xml(items))
        raise AssertionError(f"appel inattendu : {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeSource:
    """Adapter de test (contrat Phase 1) : notices sans PMID, identifiées par ``native_id``."""

    def __init__(self, metas: list[dict]) -> None:
        self.metas = {m["native_id"]: m for m in metas}

    async def search_volume(self, query: str, bornes: DateBounds | None) -> int:
        return len(self.metas)

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]:
        for nid in self.metas:
            yield nid

    async def fetch_metadata(self, ids: list[str]) -> list[dict]:
        return [dict(self.metas[i]) for i in ids if i in self.metas]

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None:
        return None


def _refuse_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"aucun appel réseau attendu : {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- §1 Modèle ---------------------------------------------------------------------------


def test_article_and_import_job_source_columns():
    """``source`` / ``native_id`` (String, NULL = donnée ancienne) ; ``ImportJob.source``."""
    a = Article.__table__.columns
    for name in ("source", "native_id"):
        assert isinstance(a[name].type, String) and a[name].nullable
    j = ImportJob.__table__.columns
    assert isinstance(j["source"].type, String) and j["source"].nullable


def test_article_source_native_unique_index():
    """Unique partiel (project_id, source, native_id) WHERE native_id IS NOT NULL."""
    ix = {i.name: i for i in Article.__table__.indexes}["uq_articles_project_source_native"]
    assert ix.unique
    assert [c.name for c in ix.columns] == ["project_id", "source", "native_id"]
    for dialect in ("postgresql", "sqlite"):
        assert "native_id IS NOT NULL" in str(ix.dialect_options[dialect]["where"])


async def test_triplet_unique_enforced_null_native_free(session):
    """Même triplet → IntegrityError ; même native_id d'une autre source, ou native_id NULL
    répété → accepté."""
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add_all(
        [
            Article(project_id=project.id, source="europepmc", native_id="X1"),
            Article(project_id=project.id, source="openalex", native_id="X1"),
            Article(project_id=project.id, source="file", native_id=None),
            Article(project_id=project.id, source="file", native_id=None),
        ]
    )
    await session.commit()
    session.add(Article(project_id=project.id, source="europepmc", native_id="X1"))
    with pytest.raises(IntegrityError):
        await session.commit()


def test_pmid_unique_index_kept():
    """Garde (D1) : l'unique (project_id, pmid) reste — dédoublonnage inter-sources par PMID."""
    assert "uq_articles_project_pmid" in {i.name for i in Article.__table__.indexes}


# --- §1 Migration 0030 ---------------------------------------------------------------------


def _migration(rev: str):
    found = sorted((BACKEND / "alembic" / "versions").glob(f"{rev}_*.py"))
    assert len(found) == 1, f"migration {rev} attendue"
    spec = importlib.util.spec_from_file_location(f"migration_{rev}", found[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0030_chain_after_0029():
    """0030 suit 0029 (traçabilité) et devient l'unique tête (rouge tant que 0029 manque)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    mod = _migration("0030")
    assert mod.revision == "0030" and mod.down_revision == "0029"
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert script.get_heads() == ["0032"]  # 0032 (recherche native_id) est la tête courante


def test_migration_0030_backfill_and_downgrade_on_sqlite(tmp_path, monkeypatch):
    """Sur une vraie base (SQLite) : backfill pubmed/PMID, jobs de recherche et de citations
    → pubmed ; triplet unique ; downgrade complet ; ré-upgrade possible."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _migration("0030")
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE articles (id VARCHAR PRIMARY KEY, project_id VARCHAR, pmid VARCHAR, "
            "doi VARCHAR)"
        )
        c.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_articles_project_pmid ON articles (project_id, pmid) "
            "WHERE pmid IS NOT NULL"
        )
        c.exec_driver_sql(
            "CREATE TABLE import_jobs (id VARCHAR PRIMARY KEY, project_id VARCHAR, "
            "kind VARCHAR, query TEXT)"
        )
        c.exec_driver_sql(
            "INSERT INTO articles VALUES ('a1', 'p', '111', '10.1/a'), ('a2', 'p', NULL, '10.1/b')"
        )
        c.exec_driver_sql(
            "INSERT INTO import_jobs VALUES ('j1', 'p', NULL, 'q'), ('j2', 'p', 'import', 'q'), "
            "('j3', 'p', 'citations', 'c'), ('j4', 'p', 'references', 'r'), "
            "('j5', 'p', 'crate', 'RO-Crate'), ('j6', 'p', 'fulltext', 'f')"
        )

    def run(step) -> None:
        with eng.begin() as c:
            monkeypatch.setattr(mod, "op", Operations(MigrationContext.configure(c)))
            step()

    run(mod.upgrade)
    with eng.connect() as c:
        arts = dict(
            (r[0], (r[1], r[2]))
            for r in c.exec_driver_sql("SELECT id, source, native_id FROM articles")
        )
        jobs = dict(c.exec_driver_sql("SELECT id, source FROM import_jobs").all())
    assert arts == {"a1": ("pubmed", "111"), "a2": (None, None)}
    assert jobs == {
        "j1": "pubmed",
        "j2": "pubmed",
        "j3": "pubmed",
        "j4": None,
        "j5": None,
        "j6": None,
    }
    ix = {i["name"]: i for i in sa.inspect(eng).get_indexes("articles")}
    assert ix["uq_articles_project_source_native"]["unique"]
    with pytest.raises(sa.exc.IntegrityError), eng.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO articles (id, project_id, pmid, source, native_id) "
            "VALUES ('a3', 'p', NULL, 'pubmed', '111')"
        )
    run(mod.downgrade)
    insp = sa.inspect(eng)
    assert {c["name"] for c in insp.get_columns("articles")} == {"id", "project_id", "pmid", "doi"}
    assert "source" not in {c["name"] for c in insp.get_columns("import_jobs")}
    assert "uq_articles_project_source_native" not in {
        i["name"] for i in insp.get_indexes("articles")
    }
    run(mod.upgrade)  # ré-applicable après downgrade


# --- §2 Import ------------------------------------------------------------------------------


async def test_pubmed_import_sets_source_native_id_and_job_source(engine):
    """Recherche PubMed : chaque article porte source='pubmed', native_id=PMID ; le job aussi."""
    project = await _make_project(engine)
    async with _ncbi([("111", None), ("222", "10.1/b")]) as http:
        result = await _import_pubmed(
            str(project.id), "cancer", session_factory=_factory(engine), http=http
        )
    assert result["imported"] == 2
    arts = await _rows(engine, Article, project.id)
    assert {(a.source, a.native_id, a.pmid) for a in arts} == {
        ("pubmed", "111", "111"),
        ("pubmed", "222", "222"),
    }
    assert all(a.origin == "pubmed" for a in arts)
    (job,) = await _rows(engine, ImportJob, project.id)
    assert job.source == "pubmed"


async def test_non_pubmed_source_keyed_on_native_id_without_pmid(engine):
    """Source sans PMID : notices indexées par native_id, article (source, native_id), pmid NULL,
    origin = la source ; job.source = la source."""
    project = await _make_project(engine)
    fake = FakeSource([{"native_id": "W1", "title": "Work 1", "doi": None}])
    async with _refuse_network() as http:
        result = await _import_pubmed(
            str(project.id),
            "q",
            session_factory=_factory(engine),
            http=http,
            adapter=fake,
            source="fakesrc",
        )
    assert (result["imported"], result["failed"]) == (1, 0)
    (art,) = await _rows(engine, Article, project.id)
    assert (art.source, art.native_id, art.pmid, art.origin) == ("fakesrc", "W1", None, "fakesrc")
    (job,) = await _rows(engine, ImportJob, project.id)
    assert job.source == "fakesrc"


async def _reset_status(engine, project_id) -> None:
    async with _factory(engine)() as session:
        project = await session.get(Project, project_id)
        project.status = ProjectStatus.DRAFT
        await session.commit()


async def test_reimport_same_triplet_without_pmid_no_duplicate(engine):
    """Ré-import d'une source sans PMID : le triplet dédoublonne (0 importé, 0 échec)."""
    project = await _make_project(engine)
    metas = [{"native_id": "W1", "title": "Work 1"}]
    for _ in range(2):
        async with _refuse_network() as http:
            result = await _import_pubmed(
                str(project.id),
                "q",
                session_factory=_factory(engine),
                http=http,
                adapter=FakeSource(metas),
                source="fakesrc",
            )
        await _reset_status(engine, project.id)
    assert (result["imported"], result["failed"]) == (0, 0)
    assert len(await _rows(engine, Article, project.id)) == 1


async def test_same_native_id_other_source_is_distinct(engine):
    """Même native_id dans deux sources (sans DOI) : deux articles distincts."""
    project = await _make_project(engine)
    for src in ("srca", "srcb"):
        async with _refuse_network() as http:
            await _import_pubmed(
                str(project.id),
                "q",
                session_factory=_factory(engine),
                http=http,
                adapter=FakeSource([{"native_id": "X1", "title": f"from {src}"}]),
                source=src,
            )
        await _reset_status(engine, project.id)
    arts = await _rows(engine, Article, project.id)
    assert sorted((a.source, a.native_id) for a in arts) == [("srca", "X1"), ("srcb", "X1")]


async def test_unknown_source_without_adapter_fails_job(engine):
    """Source absente du registre (sans adapter injecté) → LookupError, job FAILED, aucun appel."""
    project = await _make_project(engine)
    async with _refuse_network() as http:
        with pytest.raises(LookupError):
            await _import_pubmed(
                str(project.id), "q", session_factory=_factory(engine), http=http, source="nope"
            )
    (job,) = await _rows(engine, ImportJob, project.id)
    assert job.status == ImportJobStatus.FAILED


def _refs_handler():
    """Europe PMC : 10.1001/in-pubmed → PMID 111 ; Crossref : 10.1002/only-crossref."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "www.ebi.ac.uk":
            rows = [{"pmid": "111", "doi": "10.1001/in-pubmed", "title": "x"}]
            return httpx.Response(200, json={"resultList": {"result": rows}})
        if path.endswith("efetch.fcgi"):
            return httpx.Response(200, text=_efetch_xml([("111", "10.1001/in-pubmed")]))
        if host == "api.crossref.org":
            if path.endswith("10.1002/only-crossref"):
                msg = {"DOI": "10.1002/only-crossref", "title": ["Crossref title"]}
                return httpx.Response(200, json={"message": msg})
            return httpx.Response(404)
        raise AssertionError(f"appel inattendu : {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_refs_import_sets_source_per_resolution(engine):
    """Import de références : PubMed → (pubmed, PMID) ; Crossref → (crossref, DOI normalisé) ;
    fichier → (file, NULL) ; le job (sources mêlées) n'a pas de source."""
    project = await _make_project(engine)
    _, refs = parse_references("10.1001/in-pubmed\n10.1002/ONLY-crossref\n")
    refs.append(
        {"doi": None, "pmid": None, "title": "File only", "year": 2015, "authors": ["Poe E"]}
        | {k: None for k in ("journal", "abstract", "language")}
    )
    async with _refs_handler() as http:
        result = await _import_pubmed(
            str(project.id), "references", session_factory=_factory(engine), http=http, refs=refs
        )
    assert result["imported"] == 3
    by_title = {a.title: a for a in await _rows(engine, Article, project.id)}
    assert (by_title["Title 111"].source, by_title["Title 111"].native_id) == ("pubmed", "111")
    cr = by_title["Crossref title"]
    assert (cr.source, cr.native_id) == ("crossref", "10.1002/only-crossref")
    assert (by_title["File only"].source, by_title["File only"].native_id) == ("file", None)
    (job,) = await _rows(engine, ImportJob, project.id)
    assert job.source is None


def test_task_wrapper_forwards_source():
    """Le wrapper Celery propage ``source`` au core async (sans le valider : l'API le fait)."""
    with patch("app.tasks._import_pubmed", new_callable=AsyncMock) as core:
        core.return_value = {"imported": 0}
        import_pubmed_task("proj-id", "q", source="europepmc")
    assert core.await_args.kwargs["source"] == "europepmc"
