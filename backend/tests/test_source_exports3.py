"""Phase 3 multi-sources : exports qui ne mentent plus pour un article non-PubMed
(spec 2026-10-29-sources-phase3 D6) — 100 % offline."""

from types import SimpleNamespace

import app.crate as crate
import app.prisma as prisma
from app.references import to_ris


def _art(**kw):
    base = dict(
        id=1,
        project_id=None,
        pmid=None,
        pmcid=None,
        doi=None,
        title="A title",
        abstract="An abstract.",
        journal="Journal",
        pub_date="2024-05-01",
        pub_year=2024,
        authors=["Doe J"],
        pub_types=None,
        original_language="en",
        source=None,
        native_id=None,
        sections=None,
        methods_text=None,
        fulltext_source=None,
        fulltext_url=None,
        fulltext_note=None,
    )
    base |= kw
    return SimpleNamespace(**base)


# --- RIS AN -------------------------------------------------------------------


def test_ris_an_for_native_id():
    """``AN`` = PMID si dispo, sinon l'identifiant natif lisible, sinon rien."""
    a = _art(source="europepmc", native_id="W123")
    assert "AN  - Europe PMC W123" in to_ris([a])
    assert "AN  - PMID" not in to_ris([a])
    with_pmid = _art(pmid="34335492", source="pubmed", native_id="34335492")
    assert "AN  - PMID:34335492" in to_ris([with_pmid])
    # pas de PMID ni d'identifiant natif → pas de ligne AN
    out = to_ris([_art()])
    assert "AN  - " not in out


# --- RO-Crate -----------------------------------------------------------------


def test_crate_article_key_native_id():
    """Clé de dossier : pmid → doi → {source}-{native_id} → art-<uuid8>."""
    assert crate.article_key(_art(pmid="123")) == "pmid-123"
    assert crate.article_key(_art(doi="10.1000/xyz.1", source="europepmc", native_id="W123")) == (
        "doi-10-1000-xyz-1"
    )
    assert crate.article_key(_art(source="europepmc", native_id="W123")) == "europepmc-W123"
    # source inconnue/absente : préfixe « art »
    assert crate.article_key(_art(native_id="W.123")) == "art-W-123"
    assert crate.article_key(_art()).startswith("art-")


def test_crate_exports_imports():
    """project.json : clé ``imports`` (renommage propre, phase 4a) — plus de ``pubmed_queries``."""
    job = SimpleNamespace(
        query="q", source="europepmc", created_at=None, result_json={"imported": 3}
    )
    project = SimpleNamespace(
        title="P",
        description=None,
        question="Q ?",
        fields=[],
        text_sections=False,
        read_windows=None,
    )
    info = crate.project_info(project, [], [job], None, {}, None)
    assert "pubmed_queries" not in info
    assert info["imports"] == [{"query": "q", "source": "europepmc", "date": None, "imported": 3}]


# --- PRISMA -------------------------------------------------------------------


def _job(source, kind="import", imported=1):
    return SimpleNamespace(
        source=source,
        kind=kind,
        status=SimpleNamespace(value="COMPLETED"),
        result_json={"imported": imported, "failed": 0},
    )


def _arts(*sources):
    return [
        SimpleNamespace(id=i, source=s, sections=None, origin=None) for i, s in enumerate(sources)
    ]


def test_prisma_identified_label_lists_sources():
    """Le libellé « identifiées » liste les sources réellement présentes + les listes
    importées ; le cas pubmed+refs reste identique à l'actuel."""
    articles = _arts(None)  # lignes sans source (jobs antérieurs à la migration 0030)
    pubmed_refs = [_job("pubmed"), _job(None, kind="references")]
    counts = prisma.prisma_counts(articles, {}, {}, pubmed_refs)
    svg = prisma.render_svg(counts, "fr", *prisma.identified_parts(articles, pubmed_refs))
    assert "Références identifiées (PubMed, listes importées)" in svg

    multi = [_job("pubmed"), _job("europepmc"), _job(None, kind="references")]
    counts = prisma.prisma_counts(articles, {}, {}, multi)
    svg = prisma.render_svg(counts, "fr", *prisma.identified_parts(articles, multi))
    assert "Références identifiées (PubMed, Europe PMC, listes importées)" in svg
    svg_en = prisma.render_svg(counts, "en", *prisma.identified_parts(articles, multi))
    assert "Records identified (PubMed, Europe PMC, imported lists)" in svg_en

    sans_refs = _arts("europepmc")
    jobs = [_job("europepmc")]
    counts = prisma.prisma_counts(sans_refs, {}, {}, jobs)
    svg = prisma.render_svg(counts, "fr", *prisma.identified_parts(sans_refs, jobs))
    assert "Europe PMC" in svg and "listes importées" not in svg
    # garde : appelant historique (sources/lists None) → libellé statique d'avant
    assert "(PubMed, listes importées)" in prisma.render_svg(counts, "fr")
