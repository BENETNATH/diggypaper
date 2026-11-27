"""Worker-side audit fixes: LLM retry, per-queue restart recovery, stale imports,
orphan files, redelivery guard, bounded-memory full-text pass, beat schedule."""

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from celery.schedules import crontab
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import inference as inf
from app.celery_app import _consumed_queues, _mark_interrupted, celery_app
from app.db.models import (
    Article,
    ImportJob,
    ImportJobStatus,
    InferenceJob,
    InferenceJobStatus,
    Project,
    ProjectStatus,
)
from app.inference import InferenceError, OpenAICompatibleService
from app.tasks.maintenance import fail_stale_imports, purge_orphan_files


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# --- 1. infer: bounded retry on transient errors ----------------------------


@pytest.fixture
def sleeps(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(inf, "_sleep", fake_sleep)
    return waits


def _service(responses):
    calls = []

    def handler(request):
        calls.append(request)
        r = responses[min(len(calls), len(responses)) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    ok = {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
    responses = [httpx.Response(200, json=ok) if r == 200 else r for r in responses]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleService("http://llm.test/v1", http=client), calls


async def test_infer_retries_503_then_succeeds(sleeps):
    svc, calls = _service([httpx.Response(503), 200])
    assert await svc.infer("m", "s", "u") == {"ok": True}
    assert len(calls) == 2 and sleeps == [2.0]


async def test_infer_honors_retry_after_capped(sleeps):
    svc, calls = _service([httpx.Response(429, headers={"Retry-After": "3600"}), 200])
    assert await svc.infer("m", "s", "u") == {"ok": True}
    assert sleeps == [60.0]


async def test_infer_retries_timeout_then_succeeds(sleeps):
    svc, calls = _service([httpx.ReadTimeout("slow"), 200])
    assert await svc.infer("m", "s", "u") == {"ok": True}
    assert len(calls) == 2


async def test_infer_400_is_not_retried(sleeps):
    svc, calls = _service([httpx.Response(400)])
    with pytest.raises(InferenceError, match="HTTP 400"):
        await svc.infer("m", "s", "u")
    assert len(calls) == 1 and sleeps == []


async def test_infer_gives_up_after_three_attempts(sleeps):
    svc, calls = _service([httpx.Response(502)])
    with pytest.raises(InferenceError, match="HTTP 502"):
        await svc.infer("m", "s", "u")
    assert len(calls) == 3 and sleeps == [2.0, 4.0]


# --- 2. Restart recovery scoped to the worker's queues ----------------------


async def _project(session, status=ProjectStatus.PROCESSING) -> Project:
    p = Project(title="P", status=status)
    session.add(p)
    await session.commit()
    return p


async def _running_jobs(session):
    p_ingest, p_crate, p_inf = [await _project(session) for _ in range(3)]
    imp = ImportJob(project_id=p_ingest.id, query="q", status=ImportJobStatus.RUNNING)
    crate = ImportJob(
        project_id=p_crate.id, query="RO-Crate", kind="crate", status=ImportJobStatus.RUNNING
    )
    job = InferenceJob(project_id=p_inf.id, model_used="m", status=InferenceJobStatus.RUNNING)
    session.add_all([imp, crate, job])
    await session.commit()
    return imp, crate, job


async def _statuses(factory, *objs):
    async with factory() as s:
        return [(await s.get(type(o), o.id)).status for o in objs]


async def test_ingest_worker_recovers_only_ingest_imports(engine, session, factory):
    imp, crate, job = await _running_jobs(session)
    assert await _mark_interrupted(engine, queues={"ingest"}) == 1
    assert await _statuses(factory, imp, crate, job) == [
        ImportJobStatus.FAILED,
        ImportJobStatus.RUNNING,
        InferenceJobStatus.RUNNING,
    ]
    async with factory() as s:
        assert (await s.get(Project, imp.project_id)).status == ProjectStatus.DRAFT
        assert (await s.get(Project, crate.project_id)).status == ProjectStatus.PROCESSING


async def test_main_worker_leaves_ingest_imports_running(engine, session, factory):
    imp, crate, job = await _running_jobs(session)
    assert await _mark_interrupted(engine, queues={"celery"}) == 2
    assert await _statuses(factory, imp, crate, job) == [
        ImportJobStatus.RUNNING,
        ImportJobStatus.FAILED,
        InferenceJobStatus.FAILED,
    ]
    async with factory() as s:
        assert (await s.get(Project, imp.project_id)).status == ProjectStatus.PROCESSING


def test_consumed_queues_from_the_consumer():
    consumer = SimpleNamespace(task_consumer=SimpleNamespace(queues=[SimpleNamespace(name="x")]))
    assert _consumed_queues(consumer) == {"x"}
    assert _consumed_queues(None) == set(celery_app.amqp.queues.consume_from)


# --- 3. Stuck RUNNING imports (OOM-killed child) ----------------------------


async def test_stale_running_import_failed_and_project_released(session, factory):
    old_p, new_p, full_p = [await _project(session) for _ in range(3)]
    session.add(Article(project_id=full_p.id, title="a"))
    now = datetime.now(UTC)
    old = ImportJob(
        project_id=old_p.id,
        query="q",
        status=ImportJobStatus.RUNNING,
        created_at=now - timedelta(hours=30),
    )
    full = ImportJob(
        project_id=full_p.id,
        query="q",
        status=ImportJobStatus.RUNNING,
        created_at=now - timedelta(hours=30),
    )
    fresh = ImportJob(project_id=new_p.id, query="q", status=ImportJobStatus.RUNNING)
    # full-text completion: never locks the project, may legitimately run > 24 h
    long_ft = ImportJob(
        project_id=new_p.id,
        query="q",
        kind="fulltext",
        status=ImportJobStatus.RUNNING,
        created_at=now - timedelta(hours=30),
    )
    session.add_all([old, full, fresh, long_ft])
    await session.commit()
    async with factory() as s:
        assert await fail_stale_imports(s, now) == 2
    async with factory() as s:
        assert (await s.get(ImportJob, old.id)).status == ImportJobStatus.FAILED
        assert "rerun" in (await s.get(ImportJob, old.id)).error
        assert (await s.get(ImportJob, fresh.id)).status == ImportJobStatus.RUNNING
        assert (await s.get(ImportJob, long_ft.id)).status == ImportJobStatus.RUNNING
        assert (await s.get(Project, old_p.id)).status == ProjectStatus.DRAFT
        assert (await s.get(Project, full_p.id)).status == ProjectStatus.FROZEN
        assert (await s.get(Project, new_p.id)).status == ProjectStatus.PROCESSING


# --- 10. Orphan files -------------------------------------------------------


def test_purge_orphan_files(tmp_path):
    (tmp_path / "uploads").mkdir()
    old = time.time() - 30 * 3600
    paths = {
        "old_part": tmp_path / "a.zip.part",
        "running_part": tmp_path / "b.zip.part",
        "new_part": tmp_path / "c.zip.part",
        "old_upload": tmp_path / "uploads" / "crate-1.zip",  # 30 h: may still be queued
        "very_old_upload": tmp_path / "uploads" / "crate-0.zip",
        "new_upload": tmp_path / "uploads" / "crate-2.zip",
        "zip": tmp_path / "d.zip",  # finished export: the 7-day purge's business
    }
    for name, path in paths.items():
        path.write_bytes(b"x")
        if not name.startswith("new"):
            os.utime(path, (old, old))
    very_old = time.time() - 80 * 3600
    os.utime(paths["very_old_upload"], (very_old, very_old))
    assert purge_orphan_files(tmp_path, keep={"b.zip.part"}) == 2
    left = {n for n, p in paths.items() if p.exists()}
    assert left == {"running_part", "new_part", "old_upload", "new_upload", "zip"}


async def test_housekeeping_runs_the_export_purge(session, factory, tmp_path, monkeypatch):
    from app.db.models import ExportJob
    from app.tasks.maintenance import housekeeping

    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    p = await _project(session, ProjectStatus.FROZEN)
    job = ExportJob(
        project_id=p.id,
        status="COMPLETED",
        filename="e.zip",
        created_at=datetime.now(UTC) - timedelta(days=8),
    )
    session.add(job)
    await session.commit()
    (tmp_path / "e.zip").write_bytes(b"x")
    async with factory() as s:
        out = await housekeeping(s)
    assert out == {"stale_imports": 0, "orphan_files": 0}
    assert not (tmp_path / "e.zip").exists()
    async with factory() as s:
        assert await s.get(ExportJob, job.id) is None


# --- 11. acks_late redelivery guard -----------------------------------------


async def test_redelivered_pubmed_import_exits(session, factory):
    from app.tasks import _import_pubmed

    p = await _project(session, ProjectStatus.DRAFT)
    session.add(ImportJob(project_id=p.id, query="q", job_id="T1", status=ImportJobStatus.FAILED))
    await session.commit()
    assert await _import_pubmed(str(p.id), "q", session_factory=factory, job_id="T1") == {}
    async with factory() as s:
        jobs = (await s.execute(select(ImportJob).where(ImportJob.project_id == p.id))).all()
        assert len(jobs) == 1
        assert (await s.get(Project, p.id)).status == ProjectStatus.DRAFT


async def test_redelivered_crate_import_exits_and_drops_the_zip(session, factory, tmp_path):
    from app.tasks.crate_import import _import_crate

    p = await _project(session, ProjectStatus.DRAFT)
    session.add(ImportJob(project_id=p.id, query="RO-Crate", kind="crate", job_id="T2"))
    await session.commit()
    zip_path = tmp_path / "crate-x.zip"
    zip_path.write_bytes(b"x")
    out = await _import_crate(
        str(p.id), str(zip_path), str(uuid.uuid4()), session_factory=factory, job_id="T2"
    )
    assert out == {} and not zip_path.exists()


# --- 7. Bounded-memory full-text / backfill passes --------------------------


async def test_backfill_and_fulltext_selection_filtered(session, monkeypatch):
    from app.tasks import _ids_without_fulltext, backfill_metadata

    p = await _project(session, ProjectStatus.FROZEN)
    long_text = [{"kind": "methods", "text": "x" * 20000}]
    todo = Article(project_id=p.id, title="a", pmid="1")
    dated = Article(project_id=p.id, title="b", pmid="2", pub_date="2020", sections=long_text)
    no_pmid = Article(project_id=p.id, title="c")
    session.add_all([todo, dated, no_pmid])
    await session.commit()
    asked = []

    async def fake_fetch(pmids, api_key, client):
        asked.append(list(pmids))
        return [{"pmid": "1", "journal": "J", "pub_date": "2021-03"}]

    monkeypatch.setattr("app.tasks.fetch_metadata", fake_fetch)
    assert await backfill_metadata(session, p.id, None) == 1
    assert asked == [["1"]]
    await session.refresh(todo)
    assert todo.journal == "J" and todo.pub_year == 2021
    assert set(await _ids_without_fulltext(session, p.id)) == {todo.id, no_pmid.id}


# --- 9 / 12. Beat schedule and soft time limit ------------------------------


def test_beat_uses_crontab_and_no_soft_time_limit():
    sched = celery_app.conf.beat_schedule
    assert isinstance(sched["watch"]["schedule"], crontab)
    assert isinstance(sched["maintenance"]["schedule"], crontab)
    assert celery_app.conf.task_soft_time_limit is None
