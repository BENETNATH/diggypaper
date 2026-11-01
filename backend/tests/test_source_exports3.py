"""Phase 3 multi-sources: exports that no longer lie for a non-PubMed article
(spec 2026-10-29-sources-phase3 D6) — 100% offline."""

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
    """``AN`` = PMID if available, otherwise the readable native identifier, otherwise nothing."""
    a = _art(source="europepmc", native_id="W123")
    assert "AN  - Europe PMC W123" in to_ris([a])
    assert "AN  - PMID" not in to_ris([a])
    with_pmid = _art(pmid="34335492", source="pubmed", native_id="34335492")
    assert "AN  - PMID:34335492" in to_ris([with_pmid])
    # no PMID nor native identifier -> no AN row
    out = to_ris([_art()])
    assert "AN  - " not in out


# --- RO-Crate -----------------------------------------------------------------


def test_crate_article_key_native_id():
    """Folder key: pmid -> doi -> {source}-{native_id} -> art-<uuid8>."""
    assert crate.article_key(_art(pmid="123")) == "pmid-123"
    assert crate.article_key(_art(doi="10.1000/xyz.1", source="europepmc", native_id="W123")) == (
        "doi-10-1000-xyz-1"
    )
    assert crate.article_key(_art(source="europepmc", native_id="W123")) == "europepmc-W123"
    # unknown/absent source: "art" prefix
    assert crate.article_key(_art(native_id="W.123")) == "art-W-123"
    assert crate.article_key(_art()).startswith("art-")


def test_crate_exports_imports():
    """project.json: ``imports`` key (clean rename, phase 4a) — no more ``pubmed_queries``."""
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
    """The "identified" label lists the sources actually present + the imported
    lists; the pubmed+refs case stays identical to the current one."""
    articles = _arts(None)  # rows without a source (jobs prior to migration 0030)
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
    # guard: historical caller (sources/lists None) -> the old static label
    assert "(PubMed, listes importées)" in prisma.render_svg(counts, "fr")
