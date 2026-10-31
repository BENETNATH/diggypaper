"""RO-Crate import: round-trip with the real export, and rejection of suspicious zips."""

import io
import json
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    """The ``human`` entries of article 222 and the archive's text files (excluding results.xlsx)."""
    buf = io.BytesIO()
    await write_crate(session, project, "exporter@lab.fr", buf)
    with zipfile.ZipFile(buf) as z:
        files = {n: z.read(n) for n in z.namelist() if not n.endswith((".xlsx", ".pdf"))}
    return json.loads(files["articles/pmid-222/metadata.json"])["human"], files


async def test_crate_keeps_human_evidence_under_a_project_scoped_pseudonym(session, tmp_path):
    """Each exported re-read keeps its identifier, its date, its quotes and a pseudonym
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
    assert human[2]["reviewer_ref"] is None  # identity unavailable: no attribution invented
    for name, data in files.items():
        assert b"lead@lab.fr" not in data and str(user.id).encode() not in data, name
    assert b"reviewer_ref" in files["README.md"]

    # The same reviewer gets an unlinkable pseudonym in another project...
    other = Project(title="Autre", fields=FIELDS)
    session.add(other)
    await session.flush()
    other_art = Article(project_id=other.id, pmid="222")
    session.add(other_art)
    await session.flush()
    session.add(HumanAnnotation(article_id=other_art.id, user_id=user.id))
    await session.commit()
    assert (await _human_entries(session, other))[0][0]["reviewer_ref"] != ref
    # ... and the pseudonym survives account deletion (digest kept).
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
    # these results are those of the new project's initial search
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


def _patch_zip_headers(
    path: str, local_off: int, central_off: int, value: int, *, flag: bool = False
) -> None:
    """Rewrites a field of both headers (local ``PK\\x03\\x04`` + central ``PK\\x01\\x02``).

    Used to forge a dishonest zip: lying announced size (offset 22/24) or the
    the "encrypted" bit of the general purpose flag (offset 6/8)."""
    data = bytearray(Path(path).read_bytes())
    for sig, off in ((b"PK\x03\x04", local_off), (b"PK\x01\x02", central_off)):
        i = 0
        while (i := data.find(sig, i)) >= 0:
            if flag:
                struct.pack_into("<H", data, i + off, value)
            else:
                struct.pack_into("<I", data, i + off, value)
            i += 4
    Path(path).write_bytes(data)


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


# --- zip bomb: bounds on what is actually decompressed, not on the headers ---

_VALID = {"ro-crate-metadata.json": GRAPH, "project.json": '{"title": "T"}'}


def test_declared_total_over_max_total_is_rejected(tmp_path, monkeypatch):
    """Sum of the ANNOUNCED sizes > MAX_TOTAL → refused at open (fast guard)."""
    monkeypatch.setattr(ci, "MAX_TOTAL", 16)  # les 2 fichiers déclarent plus que ça
    with pytest.raises(ci.CrateError, match="too_large"):
        ci.open_crate(_zip(tmp_path, _VALID))


def test_read_budget_counts_bytes_actually_decompressed(tmp_path):
    """The cap is on the bytes actually read (counter shared by the crate)."""
    zf, _ = ci.open_crate(_zip(tmp_path, _VALID))
    try:
        ci._budget(zf).left = 5  # budget d'un crate déjà presque épuisé
        with pytest.raises(ci.CrateError, match="too_large"):
            ci._json(zf, "project.json")
        left = ci._budget(zf).left
        assert left < 0  # la lecture a bien été imputée
        # Budget exhausted: the next read is refused BEFORE any decompression
        with pytest.raises(ci.CrateError, match="too_large"):
            ci._json(zf, "ro-crate-metadata.json")
        assert ci._budget(zf).left == left  # rien n'a été décompressé (compteur inchangé)
    finally:
        zf.close()


def test_lying_header_cannot_expand_an_entry(tmp_path):
    """Lyingly small announced size: zipfile stops at the CRC → CrateError, not 500.

    Proof that nothing "decompresses beyond" the declared size: reading a lying
    entry fails (CRC) before producing the bytes, and the error is converted to
    CrateError (otherwise raw BadZipFile = 500 on POST /import-crate, broken task)."""
    path = _zip(tmp_path, _VALID)
    _patch_zip_headers(path, 22, 24, 10)  # taille décompressée annoncée : 10 octets
    with pytest.raises(ci.CrateError, match="invalid_zip"):
        ci.open_crate(path)


def test_encrypted_entry_is_rejected(tmp_path):
    """Zip "encrypted" (bit 0 of the general purpose flag) → CrateError, not raw RuntimeError."""
    path = _zip(tmp_path, _VALID)
    _patch_zip_headers(path, 6, 8, 1, flag=True)
    with pytest.raises(ci.CrateError, match="invalid_zip"):
        ci.open_crate(path)


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


# --- model traceability + version (spec 2026-10-29-tracabilite-modele-version §5) ---

DIGEST_BIG = "2" * 12 + "b" * 52
DIGEST_SMALL = "1" * 12 + "a" * 52
SNAPSHOT = {
    "main": {
        "name": "Big",
        "backend": "OLLAMA",
        "identifier": "qwen3:8b",
        "version": DIGEST_BIG,
        "quantization": "Q4_K_M",
        "parameter_size": "8.2B",
    },
    "prefilter": {
        "name": "Small",
        "backend": "OLLAMA",
        "identifier": "qwen3:1.7b",
        "version": DIGEST_SMALL,
    },
}


async def _traced_crate(session, tmp_path, model_info=SNAPSHOT, registry=()) -> str:
    """Project crate: 1 predicted article (identifier + version frozen), 1 analysis job with
    ``model_info`` (None = old job); ``registry``: ModelRegistry rows present."""
    from app.db.models import InferenceJob, InferenceJobStatus

    project = Project(title="Trace", question="Q ?", fields=FIELDS)
    session.add(project)
    await session.flush()
    art = Article(project_id=project.id, pmid="111", title="Étude A", abstract="Résumé.")
    session.add(art)
    await session.flush()
    session.add_all(list(registry))
    session.add_all(
        [
            ArticlePrediction(
                article_id=art.id,
                model_used="Big",
                model_identifier="qwen3:8b",
                model_version=DIGEST_BIG,
                decision_json={"ai": True},
                confidence_score=0.93,
                status=PredictionStatus.AUTO_ACCEPTED,
            ),
            InferenceJob(
                project_id=project.id,
                model_used="Big",
                prefilter_model="Small" if model_info and model_info.get("prefilter") else None,
                status=InferenceJobStatus.COMPLETED,
                model_info=model_info,
            ),
        ]
    )
    await session.commit()
    out = tmp_path / "trace.zip"
    with open(out, "wb") as fh:
        await write_crate(session, project, "lead@lab.fr", fh)
    return str(out)


async def test_crate_export_traces_snapshot_even_if_registry_row_deleted(session, tmp_path):
    """No registry rows (deleted models): project.json, metadata.json,
    extraction.csv and the JSON-LD graph still provide identifier + version."""
    import csv
    import io

    path = await _traced_crate(session, tmp_path)
    with zipfile.ZipFile(path) as z:
        info = json.loads(z.read("project.json"))
        rec = json.loads(z.read("articles/pmid-111/metadata.json"))
        rows = list(csv.DictReader(io.StringIO(z.read("extraction.csv").decode("utf-8-sig"))))
        graph = json.loads(z.read("ro-crate-metadata.json"))["@graph"]
    s2, s1 = info["analysis"]["stage_2_model"], info["analysis"]["stage_1_model"]
    assert (s2["name"], s2["identifier"], s2["version"]) == ("Big", "qwen3:8b", DIGEST_BIG)
    assert s2["quantization"] == "Q4_K_M" and s2["config_sha256"]
    assert (s1["name"], s1["identifier"], s1["version"]) == ("Small", "qwen3:1.7b", DIGEST_SMALL)
    assert rec["ai"]["model"] == "Big"
    assert (rec["ai"]["model_identifier"], rec["ai"]["model_version"]) == ("qwen3:8b", DIGEST_BIG)
    assert (rows[0]["model"], rows[0]["model_identifier"], rows[0]["model_version"]) == (
        "Big",
        "qwen3:8b",
        DIGEST_BIG,
    )
    apps = {e["name"]: e for e in graph if e.get("@type") == "SoftwareApplication"}
    assert apps["Big"]["softwareVersion"] == DIGEST_BIG
    assert apps["Big"]["alternateName"] == "qwen3:8b"
    assert apps["Small"]["softwareVersion"] == DIGEST_SMALL


async def test_crate_round_trip_restores_model_identifier_and_version(engine, session, tmp_path):
    """Export then import: each restored prediction keeps its identifier + version."""
    path = await _traced_crate(session, tmp_path)
    new = Project(title="Trace", status=ProjectStatus.PROCESSING)
    importer = User(email="me@lab.fr", hashed_password="x")
    session.add_all([new, importer])
    await session.commit()
    new_id = new.id
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _import_crate(str(new_id), path, str(importer.id), session_factory=factory)
    assert result["predictions"] == 1
    session.expire_all()
    pred = (
        await session.execute(
            select(ArticlePrediction)
            .join(Article, Article.id == ArticlePrediction.article_id)
            .where(Article.project_id == new_id)
        )
    ).scalar_one()
    assert (pred.model_used, pred.model_identifier, pred.model_version) == (
        "Big",
        "qwen3:8b",
        DIGEST_BIG,
    )


async def test_crate_import_old_crate_without_model_version(engine, session, tmp_path):
    """Crate produced before traceability (ai without identifier or version): import unchanged,
    fields set to None; a non-textual value is ignored (unreliable entry)."""
    meta = {
        "pmid": "9",
        "title": "Vieux",
        "ai": {"model": "Mdl", "status": "AUTO_ACCEPTED", "answers": {"ai": True}},
        "human": [],
    }
    meta2 = {**meta, "pmid": "10", "ai": {**meta["ai"], "model_version": {"x": 1}}}
    path = _zip(
        tmp_path,
        {
            "ro-crate-metadata.json": GRAPH,
            "project.json": json.dumps({"title": "Ancien", "fields": FIELDS}),
            "articles/pmid-9/metadata.json": json.dumps(meta),
            "articles/pmid-10/metadata.json": json.dumps(meta2),
        },
    )
    new = Project(title="Ancien", status=ProjectStatus.PROCESSING)
    importer = User(email="me@lab.fr", hashed_password="x")
    session.add_all([new, importer])
    await session.commit()
    new_id = new.id
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _import_crate(str(new_id), path, str(importer.id), session_factory=factory)
    assert result["predictions"] == 2
    session.expire_all()
    preds = (
        (
            await session.execute(
                select(ArticlePrediction)
                .join(Article, Article.id == ArticlePrediction.article_id)
                .where(Article.project_id == new_id)
            )
        )
        .scalars()
        .all()
    )
    assert [p.model_used for p in preds] == ["Mdl", "Mdl"]
    assert all(p.model_identifier is None and p.model_version is None for p in preds)


async def test_crate_project_json_old_job_fallback_without_url_secrets(session, tmp_path):
    """Old job (no snapshot): fallback to the registry, but the OPENAI_API identifier
    exposes only the model part + the host — no userinfo, no query, no key; version None."""
    from app.core.crypto import encrypt_secret
    from app.db.models import ModelRegistry

    reg = ModelRegistry(
        name="Big",
        backend_type="OPENAI_API",
        model_identifier="https://bob:pw-SECRET@api.x.test/v1?key=SECRET-Q#gpt-4o",
        api_key_enc=encrypt_secret("sk-SECRET-KEY"),
    )
    path = await _traced_crate(session, tmp_path, model_info=None, registry=[reg])
    with zipfile.ZipFile(path) as z:
        info = json.loads(z.read("project.json"))
        dumped = z.read("project.json").decode() + z.read("ro-crate-metadata.json").decode()
    s2 = info["analysis"]["stage_2_model"]
    assert (s2["name"], s2["backend"], s2["identifier"]) == ("Big", "OPENAI_API", "gpt-4o")
    assert s2["endpoint"] == "https://api.x.test" and s2["version"] is None
    assert "SECRET" not in dumped and "bob" not in dumped


async def test_crate_import_cleans_untrusted_model_strings(engine, session, tmp_path):
    """Unreliable entry: model, identifier and version lose their control characters
    (no `\\r\\n` at RIS export) and are truncated (200 / 500 / 200)."""
    meta = {
        "pmid": "9",
        "title": "T",
        "ai": {
            "model": "Big\r\nTY  - JOUR",
            "model_identifier": "i" * 600,
            "model_version": "}\r\nTY  - JOUR" + "v" * 300,
            "status": "AUTO_ACCEPTED",
            "answers": {"ai": True},
        },
        "human": [],
    }
    path = _zip(
        tmp_path,
        {
            "ro-crate-metadata.json": GRAPH,
            "project.json": json.dumps({"title": "T", "fields": FIELDS}),
            "articles/pmid-9/metadata.json": json.dumps(meta),
        },
    )
    new = Project(title="T", status=ProjectStatus.PROCESSING)
    importer = User(email="me@lab.fr", hashed_password="x")
    session.add_all([new, importer])
    await session.commit()
    new_id = new.id
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _import_crate(str(new_id), path, str(importer.id), session_factory=factory)
    session.expire_all()
    pred = (
        await session.execute(
            select(ArticlePrediction)
            .join(Article, Article.id == ArticlePrediction.article_id)
            .where(Article.project_id == new_id)
        )
    ).scalar_one()
    assert pred.model_used == "BigTY  - JOUR"
    assert len(pred.model_identifier) == 500
    assert pred.model_version.startswith("}TY  - JOUR") and len(pred.model_version) == 200
    assert "\r" not in pred.model_version and "\n" not in pred.model_used


async def test_crate_jsonld_model_entity_drops_null_values(session, tmp_path):
    """Old job with no snapshot and no registry: the SoftwareApplication entity has no key set to
    null (alternateName / softwareVersion absent rather than `null`)."""
    path = await _traced_crate(session, tmp_path, model_info=None)
    with zipfile.ZipFile(path) as z:
        graph = json.loads(z.read("ro-crate-metadata.json"))["@graph"]
    apps = [e for e in graph if e.get("@type") == "SoftwareApplication"]
    assert apps and all(v is not None for e in apps for v in e.values())


async def test_crate_config_sha256_covers_endpoint(session, tmp_path):
    """OPENAI_API: the same model on two endpoints yields two fingerprints; without an endpoint,
    the formula stays {name, backend, identifier}."""
    from app.crate import sha256_json

    def snap(endpoint):
        s = {"name": "GPT", "backend": "OPENAI_API", "identifier": "gpt-4o", "version": None}
        return {"main": {**s, "endpoint": endpoint} if endpoint else s, "prefilter": None}

    shas = []
    for endpoint in ("https://a.test", "https://b.test", None):
        path = await _traced_crate(session, tmp_path, snap(endpoint))
        with zipfile.ZipFile(path) as z:
            shas.append(json.loads(z.read("project.json"))["analysis"]["stage_2_model"])
    assert shas[0]["config_sha256"] != shas[1]["config_sha256"]
    cfg = {"name": "GPT", "backend": "OPENAI_API", "identifier": "gpt-4o"}
    assert shas[2]["config_sha256"] == sha256_json(cfg)
    assert shas[0]["config_sha256"] == sha256_json({**cfg, "endpoint": "https://a.test"})
