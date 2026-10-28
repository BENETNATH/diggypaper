"""Import d'un RO-Crate : aller-retour avec l'export réel, et refus des zips douteux."""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crate_import as ci
from app.api.v1.export import write_crate
from app.cleanup import delete_user
from app.db.models import (
    Article,
    ArticleFile,
    ArticlePrediction,
    HumanAnnotation,
    ImportJob,
    PredictionStatus,
    Project,
    ProjectStatus,
    User,
)
from app.searches import default_search
from app.tasks.crate_import import _import_crate

FIELDS = [{"key": "ai", "label": "AI used ?", "type": "boolean"}]
PDF = b"%PDF-1.4 fake"


async def _exported_crate(session, tmp_path) -> tuple[str, Project]:
    user = User(email="lead@lab.fr", hashed_password="x")
    session.add(user)
    project = Project(
        title="Sommeil", question="Quel outil ?", fields=FIELDS, text_sections=["all"]
    )
    session.add(project)
    await session.flush()
    a1 = Article(
        project_id=project.id,
        pmid="111",
        doi="10.1000/a",
        title="Étude A",
        abstract="Résumé A.",
        journal="J",
        pub_date="2024-05",
        pub_year=2024,
        authors=["Roe J", "Poe E"],
        fulltext_source="pmc",
        sections=[
            {"title": "Methods", "kind": "methods", "text": "Mice were housed."},
            {"title": "Results", "kind": "results", "text": "Sleep improved."},
        ],
    )
    a2 = Article(
        project_id=project.id, pmid="222", title="Étude B", abstract="Seulement un résumé."
    )
    session.add_all([a1, a2])
    await session.flush()
    session.add(ArticleFile(article_id=a1.id, data=PDF))
    initial = await default_search(session, project)
    session.add(
        ArticlePrediction(
            article_id=a1.id,
            search_id=initial.id,
            model_used="Mdl",
            decision_json={
                "ai": True,
                "field_confidence": {"ai": 0.9},
                "reasoning_summary": "Because.",
            },
            confidence_score=0.93,
            evidence_quotes=["Mice were housed."],
            status=PredictionStatus.AUTO_ACCEPTED,
        )
    )
    session.add(
        HumanAnnotation(
            article_id=a2.id,
            user_id=user.id,
            search_id=initial.id,
            decision_json={"ai": False},
            notes="vu",
        )
    )
    await session.commit()
    out = tmp_path / "crate.zip"
    with open(out, "wb") as fh:
        await write_crate(session, project, user.email, fh)
    return str(out), project


async def _human_entries(session, project) -> tuple[list[dict], dict[str, bytes]]:
    """Entrées ``human`` de l'article 222 et fichiers texte de l'archive (hors results.xlsx)."""
    buf = io.BytesIO()
    await write_crate(session, project, "exporter@lab.fr", buf)
    with zipfile.ZipFile(buf) as z:
        files = {n: z.read(n) for n in z.namelist() if not n.endswith((".xlsx", ".pdf"))}
    return json.loads(files["articles/pmid-222/metadata.json"])["human"], files


async def test_crate_keeps_human_evidence_under_a_project_scoped_pseudonym(session, tmp_path):
    """Chaque relecture exportée garde son identifiant, sa date, ses citations et un pseudonyme
    propre au projet ; ni e-mail ni identifiant de compte n'apparaissent dans les JSON/Markdown."""
    _, project = await _exported_crate(session, tmp_path)
    user = (await session.execute(select(User).where(User.email == "lead@lab.fr"))).scalar_one()
    art = (await session.execute(select(Article).where(Article.pmid == "222"))).scalar_one()
    quotes = ['"Sleep" improved 🐁\nN = 12', "  exact spacing  ", "dup", "dup"]
    later = datetime.now(UTC) + timedelta(seconds=1)
    second = HumanAnnotation(
        article_id=art.id,
        user_id=user.id,
        decision_json={"ai": True, "evidence_quotes": quotes},
        notes="revu",
        created_at=later,
    )
    legacy = HumanAnnotation(  # relecteur inconnu (ni compte, ni empreinte)
        article_id=art.id, decision_json={"ai": True}, created_at=later + timedelta(seconds=1)
    )
    session.add_all([second, legacy])
    await session.commit()
    anns = (
        (
            await session.execute(
                select(HumanAnnotation)
                .where(HumanAnnotation.article_id == art.id)
                .order_by(HumanAnnotation.created_at, HumanAnnotation.id)
            )
        )
        .scalars()
        .all()
    )

    human, files = await _human_entries(session, project)
    assert [h["annotation_id"] for h in human] == [str(a.id) for a in anns]
    assert [h["created_at"] for h in human] == [a.created_at.isoformat() for a in anns]
    assert [h["evidence_quotes"] for h in human] == [None, quotes, None]
    assert [h["answers"] for h in human] == [{"ai": False}, {"ai": True}, {"ai": True}]
    assert [h["notes"] for h in human] == ["vu", "revu", None]
    ref = human[0]["reviewer_ref"]
    assert ref and ref.startswith("reviewer-") and human[1]["reviewer_ref"] == ref
    assert human[2]["reviewer_ref"] is None  # identité indisponible : pas d'attribution inventée
    for name, data in files.items():
        assert b"lead@lab.fr" not in data and str(user.id).encode() not in data, name
    assert b"reviewer_ref" in files["README.md"]

    # Le même relecteur reçoit un pseudonyme sans lien dans un autre projet…
    other = Project(title="Autre", fields=FIELDS)
    session.add(other)
    await session.flush()
    other_art = Article(project_id=other.id, pmid="222")
    session.add(other_art)
    await session.flush()
    session.add(HumanAnnotation(article_id=other_art.id, user_id=user.id))
    await session.commit()
    assert (await _human_entries(session, other))[0][0]["reviewer_ref"] != ref
    # … et le pseudonyme survit à la suppression du compte (empreinte conservée).
    await delete_user(session, user)
    await session.commit()
    after, _ = await _human_entries(session, project)
    assert [h["reviewer_ref"] for h in after] == [ref, ref, None]
    assert after[1]["evidence_quotes"] == quotes


async def test_round_trip_restores_corpus_results_and_pdf(engine, session, tmp_path):
    path, source = await _exported_crate(session, tmp_path)
    new = Project(title="Sommeil", status=ProjectStatus.PROCESSING, created_by=source.created_by)
    session.add(new)
    importer = User(email="me@lab.fr", hashed_password="x")
    session.add(importer)
    await session.commit()
    new_id, importer_id = new.id, importer.id
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _import_crate(str(new_id), path, str(importer_id), session_factory=factory)
    assert result["imported"] == 2 and result["failed"] == 0 and result["pdf"] == 1
    assert result["predictions"] == 1 and result["reviews"] == 1 and result["fulltext"] == 1

    session.expire_all()
    project = await session.get(Project, new_id)
    assert project.status == ProjectStatus.FROZEN and project.question == "Quel outil ?"
    assert [f["key"] for f in project.fields] == ["ai"]
    arts = {
        a.pmid: a
        for a in (
            await session.execute(select(Article).where(Article.project_id == new_id))
        ).scalars()
    }
    a1, a2 = arts["111"], arts["222"]
    assert (a1.title, a1.doi, a1.journal, a1.pub_year, a1.authors) == (
        "Étude A",
        "10.1000/a",
        "J",
        2024,
        ["Roe J", "Poe E"],
    )
    assert [s["kind"] for s in a1.sections] == [
        "methods",
        "results",
    ]  # le résumé n'est pas une section
    assert a1.methods_text == "Mice were housed." and a1.abstract == "Résumé A."
    assert a1.origin == "crate" and a2.sections is None and a2.abstract == "Seulement un résumé."
    pdf = (
        await session.execute(select(ArticleFile.data).where(ArticleFile.article_id == a1.id))
    ).scalar_one()
    assert pdf == PDF
    pred = (
        await session.execute(
            select(ArticlePrediction).where(ArticlePrediction.article_id == a1.id)
        )
    ).scalar_one()
    assert pred.model_used == "Mdl" and pred.status == PredictionStatus.AUTO_ACCEPTED
    assert pred.decision_json["ai"] is True and pred.decision_json["field_confidence"] == {
        "ai": 0.9
    }
    assert pred.evidence_quotes == ["Mice were housed."] and pred.confidence_score == pytest.approx(
        0.93
    )
    ann = (
        await session.execute(select(HumanAnnotation).where(HumanAnnotation.article_id == a2.id))
    ).scalar_one()
    assert ann.decision_json == {"ai": False} and ann.user_id == importer_id and ann.notes == "vu"
    # ces résultats sont ceux de la recherche initiale du nouveau projet
    initial = await default_search(session, project)
    assert pred.search_id == initial.id == ann.search_id
    job = (
        await session.execute(select(ImportJob).where(ImportJob.project_id == new_id))
    ).scalar_one()
    assert job.kind == "crate" and job.status.value == "COMPLETED"
    import os

    assert not os.path.exists(path)  # le zip déposé est supprimé


def _zip(tmp_path, files: dict[str, bytes | str], name="x.zip") -> str:
    out = tmp_path / name
    with zipfile.ZipFile(out, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return str(out)


GRAPH = json.dumps({"@graph": [{"conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}}]})


def test_open_crate_rejects_what_is_not_a_diggypaper_crate(tmp_path):
    notzip = tmp_path / "a.zip"
    notzip.write_bytes(b"hello")
    with pytest.raises(ci.CrateError, match="not_a_zip"):
        ci.open_crate(notzip)
    with pytest.raises(ci.CrateError, match="missing:ro-crate-metadata.json"):
        ci.open_crate(_zip(tmp_path, {"project.json": "{}"}))
    with pytest.raises(ci.CrateError, match="not_an_ro_crate"):
        ci.open_crate(
            _zip(tmp_path, {"ro-crate-metadata.json": '{"@graph": []}', "project.json": "{}"})
        )
    with pytest.raises(ci.CrateError, match="no_title"):
        ci.open_crate(_zip(tmp_path, {"ro-crate-metadata.json": GRAPH, "project.json": "{}"}))
    with pytest.raises(ci.CrateError, match="unsafe_path"):
        ci.open_crate(
            _zip(
                tmp_path,
                {"../evil": "x", "ro-crate-metadata.json": GRAPH, "project.json": '{"title": "T"}'},
            )
        )
    zf, info = ci.open_crate(
        _zip(tmp_path, {"ro-crate-metadata.json": GRAPH, "project.json": '{"title": "T"}'})
    )
    assert info["title"] == "T"
    zf.close()


def test_markdown_sections_and_bad_fields():
    secs = ci.sections_from_markdown(
        "# T\n\n## Abstract\n\nabs\n\n## Materials and Methods\n\nA\nB\n\n## Discussion\n\nC"
    )
    assert [(s["title"], s["kind"], s["text"]) for s in secs] == [
        ("Materials and Methods", "methods", "A B"),
        ("Discussion", "discussion", "C"),
    ]
    with pytest.raises(ci.CrateError, match="invalid_fields"):
        ci.project_definition({"fields": [{"key": "x", "type": "nope"}]})


async def test_import_endpoint(client, session, researcher_user, do_login, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import app.api.v1.crate_import as route

    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    delay = MagicMock(return_value=MagicMock(id="job-1"))
    monkeypatch.setattr(route.import_crate_task, "delay", delay)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    good = _zip(
        tmp_path,
        {"ro-crate-metadata.json": GRAPH, "project.json": '{"title": "Restauré"}'},
        "g.zip",
    )
    with open(good, "rb") as fh:
        r = await client.post("/api/v1/projects/import-crate", content=fh.read())
    assert r.status_code == 202, r.text
    pid = r.json()["project_id"]
    project = await session.get(Project, __import__("uuid").UUID(pid))
    assert project.title == "Restauré" and project.status == ProjectStatus.PROCESSING
    path = delay.call_args.args[1]
    assert path.startswith(str(tmp_path)) and delay.call_args.args[0] == pid
    bad = await client.post("/api/v1/projects/import-crate", content=b"not a zip")
    assert bad.status_code == 422 and "invalid_crate" in bad.json()["detail"]
    assert len(list((tmp_path / "uploads").glob("crate-*.zip"))) == 1  # le zip refusé est supprimé
