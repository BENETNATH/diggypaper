"""Corpus watch (spec 2026-11-07-veille-automatisee): match rule, API CRUD + authZ,
one pass (re-push / dedup / import origin=watch / inference launch), the post-
inference alert, and PRISMA traceability. Offline: a fake adapter + an injected
launcher; the import unit (_insert_article) is exercised for real.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    SearchArticle,
    User,
    UserRole,
    Watch,
)
from app.prisma import prisma_counts
from app.searches import default_search
from app.tasks.watch import (
    _resolve_model,
    article_matches,
    evaluate_watch_matches,
    is_positive,
    primary_field,
    run_watch_pass,
)

FIELDS = [{"key": "ai", "label": "Uses AI", "type": "boolean"}]


def ArticlePrediction_row(article_id, search_id, decision):
    return ArticlePrediction(
        article_id=article_id,
        search_id=search_id,
        model_used="m",
        decision_json=decision,
        confidence_score=(decision or {}).get("confidence_score"),
        status=PredictionStatus.NEEDS_HUMAN_REVIEW,
    )


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
async def watch_project(session, researcher_user, client, do_login):
    """A LEAD project with its default search (question + one boolean field)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = Project(title="P", created_by=researcher_user.id, question="Q ?", fields=FIELDS)
    session.add(project)
    await session.commit()
    await default_search(session, project)
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    search = (
        (await session.execute(select(Search).where(Search.project_id == project.id)))
        .scalars()
        .one()
    )
    return project, search, researcher_user


# --- Match rule (pure) ------------------------------------------------------


def test_primary_field_is_the_first_or_marked():
    assert primary_field([{"key": "a"}, {"key": "b", "primary": True}])["key"] == "b"
    assert primary_field([{"key": "a"}, {"key": "b"}])["key"] == "a"
    assert primary_field(None) is None


def test_is_positive():
    assert (
        is_positive("oui", "choice")
        and is_positive("yes", "choice")
        and is_positive("include", "choice")
    )
    assert (
        is_positive(True, "boolean")
        and is_positive("true", "boolean")
        and is_positive("1", "boolean")
    )
    assert (
        not is_positive("non", "choice")
        and not is_positive(False, "boolean")
        and not is_positive(None, None)
    )


def test_match_primary_positive():
    assert article_matches({"ai": True, "confidence_score": 0.2}, FIELDS[0], 0.5) is True
    assert article_matches({"ai": "oui", "confidence_score": 0.1}, FIELDS[0], 0.5) is True


def test_match_primary_negative_low_confidence():
    assert article_matches({"ai": False, "confidence_score": 0.2}, FIELDS[0], 0.5) is False


def test_match_high_confidence_alone():
    assert article_matches({"confidence_score": 0.9}, FIELDS[0], 0.5) is True


def test_match_threshold_respected():
    assert article_matches({"confidence_score": 0.4}, FIELDS[0], 0.6) is False
    assert article_matches({"confidence_score": 0.6}, FIELDS[0], 0.6) is True


def test_match_empty_decision_is_no_match():
    assert article_matches(None, FIELDS[0], 0.5) is False
    assert article_matches({}, FIELDS[0], 0.5) is False


# --- API CRUD + authZ -------------------------------------------------------


async def test_create_watch_lead_201(client, watch_project):
    project, search, _ = watch_project
    r = await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "pubmed", "query": "rat phenotyping", "search_id": str(search.id)},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "pubmed" and body["status"] == "ACTIVE"
    assert body["search"]["id"] == str(search.id) and body["match_threshold"] is None


async def test_create_watch_unknown_source_422(client, watch_project):
    project, search, _ = watch_project
    r = await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "nope", "query": "q", "search_id": str(search.id)},
    )
    assert r.status_code == 422, r.text


async def test_create_watch_unknown_search_404(client, watch_project):
    project, _, _ = watch_project
    r = await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "pubmed", "query": "q", "search_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text


async def test_create_watch_forbidden_for_non_lead(client, session, watch_project, do_login):
    project, search, _ = watch_project
    # a project member who is NOT the lead
    from app.core.security import hash_password

    u = User(
        email="annotator@lab.fr",
        hashed_password=hash_password("annotator-pass-123"),
        role=UserRole.RESEARCHER,
    )
    session.add(u)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=u.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    await session.commit()
    await do_login("annotator@lab.fr", "annotator-pass-123", consent=True)
    r = await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
    )
    assert r.status_code == 403, r.text


async def test_list_watches_as_member(client, watch_project):
    project, search, _ = watch_project
    await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
    )
    r = await client.get(f"/api/v1/projects/{project.id}/watches")
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


async def test_pause_and_resume_watch(client, watch_project):
    project, search, _ = watch_project
    wid = (
        await client.post(
            f"/api/v1/projects/{project.id}/watches",
            json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
        )
    ).json()["id"]
    r = await client.patch(
        f"/api/v1/projects/{project.id}/watches/{wid}", json={"status": "PAUSED"}
    )
    assert r.status_code == 200 and r.json()["status"] == "PAUSED"
    r = await client.patch(
        f"/api/v1/projects/{project.id}/watches/{wid}", json={"status": "ACTIVE"}
    )
    assert r.json()["status"] == "ACTIVE"


async def test_update_threshold_and_notify_all(client, watch_project):
    project, search, _ = watch_project
    wid = (
        await client.post(
            f"/api/v1/projects/{project.id}/watches",
            json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
        )
    ).json()["id"]
    r = await client.patch(
        f"/api/v1/projects/{project.id}/watches/{wid}",
        json={"match_threshold": 0.7, "notify_all": True},
    )
    assert r.json()["match_threshold"] == 0.7 and r.json()["notify_all"] is True


async def test_delete_watch_204_and_404(client, watch_project):
    project, search, _ = watch_project
    wid = (
        await client.post(
            f"/api/v1/projects/{project.id}/watches",
            json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
        )
    ).json()["id"]
    assert (await client.delete(f"/api/v1/projects/{project.id}/watches/{wid}")).status_code == 204
    assert (await client.delete(f"/api/v1/projects/{project.id}/watches/{wid}")).status_code == 404


# --- One pass (fake adapter, real import unit, injected launcher) -----------


class FakeAdapter:
    def __init__(self, ids):
        self.ids = ids
        self.metas = {
            i: {"native_id": i, "title": f"T{i}", "pmid": f"P{i}", "abstract": "x"} for i in ids
        }

    async def iter_ids(self, query, bornes, limit=None):
        for i in self.ids[: (limit or len(self.ids))]:
            yield i

    async def fetch_metadata(self, batch):
        return [self.metas[i] for i in batch if i in self.metas]

    async def fetch_fulltext(self, id):
        return None


class FakeLauncher:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, watch, project, article_ids, user):
        ps = Search(
            project_id=project.id,
            name="pass",
            lot_all=False,
            fields=watch.search.fields,
            question="Q",
        )
        db.add(ps)
        await db.flush()
        db.add_all(SearchArticle(search_id=ps.id, article_id=i) for i in article_ids)
        job = InferenceJob(
            project_id=project.id,
            search_id=ps.id,
            model_used="m",
            status=InferenceJobStatus.PENDING,
        )
        db.add(job)
        await db.flush()  # assign job.id
        watch.inference_job_id = job.id
        await db.commit()
        await db.refresh(job)
        self.calls.append((ps.id, list(article_ids), job.id))
        return job


async def test_pass_imports_fresh_with_origin_watch(session, watch_project, client):
    project, search, _ = watch_project
    w = Watch(
        project_id=project.id,
        search_id=search.id,
        source="pubmed",
        query="q",
        created_by=search.created_by,
    )
    session.add(w)
    await session.commit()
    # one pre-existing article with the same identity (dedup must skip it)
    session.add(
        Article(project_id=project.id, source="pubmed", native_id="1", pmid="P1", title="old")
    )
    await session.commit()
    launcher = FakeLauncher()
    res = await run_watch_pass(
        session, w, client=None, adapter=FakeAdapter(["1", "2", "3"]), launch_inference=launcher
    )
    assert res["found"] == 3 and res["fresh"] == 2 and res["imported"] == 2
    origins = {
        (a.native_id, a.origin)
        for a in (
            await session.execute(select(Article).where(Article.project_id == project.id))
        ).scalars()
    }
    assert ("2", "watch") in origins and ("3", "watch") in origins
    assert ("1", "watch") not in origins  # dedup: the pre-existing one is not re-imported
    assert launcher.calls and len(launcher.calls[0][1]) == 2


async def test_pass_no_new_no_inference(session, watch_project):
    project, search, _ = watch_project
    w = Watch(
        project_id=project.id,
        search_id=search.id,
        source="pubmed",
        query="q",
        created_by=search.created_by,
    )
    session.add(w)
    await session.commit()
    launcher = FakeLauncher()
    res = await run_watch_pass(
        session, w, client=None, adapter=FakeAdapter([]), launch_inference=launcher
    )
    assert res["found"] == 0 and res["imported"] == 0 and res["job_id"] is None
    assert w.last_matched["found"] == 0 and w.last_matched["matched"] == 0
    assert not launcher.calls
    assert w.last_run_at is not None  # advanced after the re-push even with no new


async def test_pass_last_run_at_advanced_after_repush(session, watch_project):
    project, search, _ = watch_project
    w = Watch(
        project_id=project.id,
        search_id=search.id,
        source="pubmed",
        query="q",
        created_by=search.created_by,
    )
    session.add(w)
    await session.commit()
    assert w.last_run_at is None
    await run_watch_pass(
        session, w, client=None, adapter=FakeAdapter(["9"]), launch_inference=FakeLauncher()
    )
    assert w.last_run_at is not None


# --- Post-inference alert (match + e-mail) ----------------------------------


async def _seed_pass_with_predictions(
    session, watch_project, decisions, notify_all=False, threshold=None
):
    """Creates a watch + a pass search + a job + articles + predictions, then returns
    (watch, job, articles)."""
    project, search, _ = watch_project
    w = Watch(
        project_id=project.id,
        search_id=search.id,
        source="pubmed",
        query="q",
        created_by=search.created_by,
        notify_all=notify_all,
        match_threshold=threshold,
    )
    session.add(w)
    await session.commit()
    ps = Search(project_id=project.id, name="pass", lot_all=False, fields=FIELDS, question="Q")
    session.add(ps)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        search_id=ps.id,
        model_used="m",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add(job)
    await session.flush()  # assign job.id (Python-side uuid4 default)
    w.inference_job_id = job.id
    arts = []
    for i, dec in enumerate(decisions, start=1):
        a = Article(
            project_id=project.id, source="pubmed", native_id=str(i), pmid=f"P{i}", title=f"T{i}"
        )
        session.add(a)
        await session.flush()
        session.add(ArticlePrediction_row(a.id, ps.id, dec))
        session.add(SearchArticle(search_id=ps.id, article_id=a.id))
        arts.append(a)
    await session.commit()
    return w, job, arts


class RecordingMailer:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, to, subject, body):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append({"to": to, "subject": subject, "body": body})


async def test_match_sends_email_to_lead(session, watch_project, mock_mailer):
    w, job, arts = await _seed_pass_with_predictions(
        session,
        watch_project,
        [{"ai": True, "confidence_score": 0.9}, {"ai": False, "confidence_score": 0.1}],
    )
    out = await evaluate_watch_matches(
        session, job, mailer=mock_mailer, app_base_url="https://app.test"
    )
    assert out["matched"] == 1
    assert len(mock_mailer.sent) == 1
    m = mock_mailer.sent[0]
    assert m["to"] == "researcher@lab.fr"
    assert "T1" in m["body"] and "https://app.test/projects/" in m["body"]
    assert w.last_matched["matched"] == 1 and len(w.last_matched["matched_ids"]) == 1
    # the last matching article is stored for the UI card (title + confidence)
    assert w.last_matched["matched_details"] == [
        {"id": str(arts[0].id), "title": "T1", "pmid": "P1", "confidence": 0.9}
    ]


async def test_no_match_silence_when_notify_all_false(session, watch_project, mock_mailer):
    w, job, _ = await _seed_pass_with_predictions(
        session, watch_project, [{"ai": False, "confidence_score": 0.1}], notify_all=False
    )
    out = await evaluate_watch_matches(session, job, mailer=mock_mailer)
    assert out["matched"] == 0
    assert mock_mailer.sent == []  # silence


async def test_no_match_email_when_notify_all_true(session, watch_project, mock_mailer):
    w, job, _ = await _seed_pass_with_predictions(
        session, watch_project, [{"ai": False, "confidence_score": 0.1}], notify_all=True
    )
    out = await evaluate_watch_matches(session, job, mailer=mock_mailer)
    assert out["matched"] == 0
    assert len(mock_mailer.sent) == 1  # short "imported, no match" note


async def test_smtp_failure_is_best_effort(session, watch_project):
    w, job, _ = await _seed_pass_with_predictions(
        session, watch_project, [{"ai": True, "confidence_score": 0.9}]
    )
    out = await evaluate_watch_matches(session, job, mailer=RecordingMailer(fail=True))
    # no exception; match still recorded
    assert out["matched"] == 1
    assert w.last_matched["matched"] == 1


async def test_no_watch_for_job_returns_empty(session, watch_project):
    project, search, _ = watch_project
    job = InferenceJob(
        project_id=project.id,
        search_id=search.id,
        model_used="m",
        status=InferenceJobStatus.COMPLETED,
    )
    session.add(job)
    await session.commit()
    out = await evaluate_watch_matches(session, job, mailer=RecordingMailer())
    assert out == {}


# --- PRISMA traceability ----------------------------------------------------


async def test_prisma_counts_via_watch(session, watch_project):
    project, search, _ = watch_project
    for i, origin in (("1", "watch"), ("2", "watch"), ("3", "pubmed")):
        session.add(
            Article(
                project_id=project.id,
                source="pubmed",
                native_id=i,
                pmid=f"P{i}",
                title=f"T{i}",
                origin=origin,
            )
        )
    await session.commit()
    arts = list(
        (await session.execute(select(Article).where(Article.project_id == project.id))).scalars()
    )
    counts = prisma_counts(arts, {}, {}, [])
    assert counts["via_watch"] == 2
    assert counts["identified"] >= 3


# --- Manual run rate-limit ---------------------------------------------------


async def test_run_watch_rate_limited_6th_429(client, session, watch_project, monkeypatch):
    """6th manual pass same IP → 429 (limit 5/15min); the pass is stubbed (no network)."""
    project, search, _ = watch_project

    async def fake_run(db, watch, launched_by=None):
        return {}

    monkeypatch.setattr("app.api.v1.watches.run_watch_now", fake_run)
    wid = (
        await client.post(
            f"/api/v1/projects/{project.id}/watches",
            json={"source": "pubmed", "query": "q", "search_id": str(search.id)},
        )
    ).json()["id"]
    codes = []
    for _ in range(6):
        r = await client.post(f"/api/v1/projects/{project.id}/watches/{wid}/run")
        codes.append(r.status_code)
    assert codes[:5] == [200] * 5 and codes[5] == 429
    assert (await client.post(f"/api/v1/projects/{project.id}/watches/{wid}/run")).json()[
        "detail"
    ] == "rate_limited"


# --- Search without criteria (no question, no fields) -----------------------


async def _bare_search(session, project) -> Search:
    """A degenerate search: no question, no answer fields."""
    s = Search(project_id=project.id, name="Vide", question=None, fields=None, kind="standard")
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return s


class _AdapterBoom:
    def iter_ids(self, *a, **k):
        raise AssertionError("la source ne doit pas être requêtée sans critères")


async def test_create_watch_search_without_criteria_422(client, session, watch_project):
    """Creating a watch on a search with no question/fields → 422 (a watch with nothing
    to match against is useless)."""
    project, _, _ = watch_project
    s = await _bare_search(session, project)
    r = await client.post(
        f"/api/v1/projects/{project.id}/watches",
        json={"source": "pubmed", "query": "q", "search_id": str(s.id)},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "search_without_criteria"


async def test_run_watch_search_without_criteria_422(client, session, watch_project):
    """A pre-existing watch whose search lost its criteria: run → 422 (not a 500)."""
    project, _, _ = watch_project
    s = await _bare_search(session, project)
    w = Watch(project_id=project.id, search_id=s.id, source="pubmed", query="q")
    session.add(w)
    await session.commit()
    r = await client.post(f"/api/v1/projects/{project.id}/watches/{w.id}/run")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "search_without_criteria"


async def test_pass_without_criteria_raises_before_source(session, watch_project):
    """run_watch_pass validates the criteria before re-pushing the source query."""
    project, _, _ = watch_project
    s = await _bare_search(session, project)
    w = Watch(project_id=project.id, search_id=s.id, source="pubmed", query="q")
    session.add(w)
    await session.commit()
    with pytest.raises(ValueError, match="criteria"):
        await run_watch_pass(session, w, adapter=_AdapterBoom())


# --- Model resolution (active models only) ----------------------------------


def _model(name, active):
    return ModelRegistry(
        name=name, backend_type=BackendType.OLLAMA, model_identifier=name, is_active=active
    )


async def test_resolve_model_fallback_skips_inactive(session, watch_project):
    """The fallback picks the first ACTIVE model — inactive ones (e.g. a retired
    Laya) must never be launched."""
    project, search, _ = watch_project
    session.add(_model("aaa-inactive", False))
    session.add(_model("bbb-active", True))
    await session.commit()
    w = Watch(project_id=project.id, search_id=search.id, source="pubmed", query="q")
    session.add(w)
    await session.commit()
    assert await _resolve_model(session, project, w) == "bbb-active"


async def test_resolve_model_last_job_inactive_falls_back(session, watch_project):
    """A previous job used a model that is now inactive: fall back to the first
    active model instead of re-launching the dead one."""
    project, search, _ = watch_project
    session.add(_model("dead-model", False))
    session.add(_model("alive-model", True))
    await session.commit()
    w = Watch(project_id=project.id, search_id=search.id, source="pubmed", query="q")
    session.add(w)
    await session.commit()
    session.add(
        InferenceJob(
            project_id=project.id,
            search_id=search.id,
            model_used="dead-model",
            status=InferenceJobStatus.COMPLETED,
        )
    )
    await session.commit()
    assert await _resolve_model(session, project, w) == "alive-model"


async def test_real_launcher_persists_job_link(session, watch_project, monkeypatch):
    """The REAL launcher must persist watch.inference_job_id: job.id only exists
    after the flush, so the assignment has to come after it (the fake launcher in
    the other tests masks this)."""
    project, search, _ = watch_project
    session.add(_model("real-m", True))
    await session.commit()
    w = Watch(project_id=project.id, search_id=search.id, source="pubmed", query="q")
    session.add(w)
    await session.commit()
    delays = []
    monkeypatch.setattr(
        "app.tasks.inference.inference_task.delay", lambda job_id: delays.append(job_id)
    )
    res = await run_watch_pass(session, w, adapter=FakeAdapter(["1", "2"]))
    assert res["job_id"] is not None
    await session.refresh(w)
    assert str(w.inference_job_id) == res["job_id"]
    assert delays == [res["job_id"]]
