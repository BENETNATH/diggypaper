"""Entretien : alerte disque (admins), projets inactifs (avertir puis supprimer), « Conserver »."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import maintenance
from app.core.config import get_settings
from app.db.models import (
    Article,
    ArticleFile,
    ImportJob,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)


class FakeMailer:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to, subject, body):
        self.sent.append((to, subject, body))


@pytest.fixture
def exports(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    return tmp_path


async def _lead_project(session, title="Vieux projet", age_days=40):
    lead = User(
        email=f"{uuid.uuid4().hex[:6]}@lab.fr", hashed_password="x", language_preference="fr"
    )
    old = datetime.now(UTC) - timedelta(days=age_days)
    project = Project(title=title, created_at=old)
    session.add_all([lead, project])
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=lead.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    session.add(Article(project_id=project.id, pmid="1", title="a"))
    await session.commit()
    return lead, project


async def test_disk_alert_goes_to_admins_once_a_day(session, exports, monkeypatch):
    session.add(
        User(
            email="admin@lab.fr", hashed_password="x", role=UserRole.ADMIN, language_preference="fr"
        )
    )
    await session.commit()
    mailer = FakeMailer()
    monkeypatch.setattr(get_settings(), "disk_alert_percent", 0)  # toujours « plein »
    assert maintenance.disk_status()["low"] is True
    assert await maintenance.disk_alert(session, mailer) is True
    assert [m[0] for m in mailer.sent] == ["admin@lab.fr"]
    assert "espace disque faible" in mailer.sent[0][1]
    assert await maintenance.disk_alert(session, mailer) is False  # déjà prévenu aujourd'hui
    old = os.path.getmtime(exports / ".disk_alert") - 2 * 86400
    os.utime(exports / ".disk_alert", (old, old))
    assert await maintenance.disk_alert(session, mailer) is True  # le lendemain
    monkeypatch.setattr(get_settings(), "disk_alert_percent", 101)
    monkeypatch.setattr(get_settings(), "disk_alert_free_gb", 0)
    assert maintenance.disk_status()["low"] is False
    assert await maintenance.disk_alert(session, mailer) is False


async def test_retention_disabled_by_default(session, exports):
    await _lead_project(session)
    mailer = FakeMailer()
    assert await maintenance.retention_sweep(session, mailer) == {"warned": 0, "deleted": 0}
    assert mailer.sent == []


async def test_inactive_project_warned_then_deleted_unless_active(session, exports, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "project_retention_days", 30)
    monkeypatch.setattr(s, "project_retention_grace_days", 14)
    lead, project = await _lead_project(session)
    _, fresh = await _lead_project(session, "Projet actif", age_days=3)
    mailer = FakeMailer()
    now = datetime.now(UTC)

    out = await maintenance.retention_sweep(session, mailer, now)
    assert out == {"warned": 1, "deleted": 0}
    to, subject, body = mailer.sent[0]
    assert to == lead.email and "Vieux projet" in subject and f"/projects/{project.id}" in body
    assert (await session.get(Project, fresh.id)).retention_warned_at is None

    # Pendant le délai de grâce : rien de plus (ni second e-mail, ni suppression)
    assert await maintenance.retention_sweep(session, mailer, now + timedelta(days=5)) == {
        "warned": 0,
        "deleted": 0,
    }
    assert len(mailer.sent) == 1

    # Activité après l'avertissement : on repart de zéro, pas de suppression
    session.add(ImportJob(project_id=project.id, query="q", created_at=now + timedelta(days=6)))
    await session.commit()
    out = await maintenance.retention_sweep(session, mailer, now + timedelta(days=20))
    assert out == {"warned": 0, "deleted": 0}
    assert (await session.get(Project, project.id)).retention_warned_at is None


async def test_warned_project_deleted_after_grace(session, exports, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "project_retention_days", 30)
    monkeypatch.setattr(s, "project_retention_grace_days", 14)
    lead, project = await _lead_project(session)
    pid = project.id
    mailer = FakeMailer()
    now = datetime.now(UTC)
    await maintenance.retention_sweep(session, mailer, now)
    out = await maintenance.retention_sweep(session, mailer, now + timedelta(days=15))
    assert out == {"warned": 0, "deleted": 1}
    session.expire_all()
    assert await session.get(Project, pid) is None
    assert (await session.execute(select(Article).where(Article.project_id == pid))).first() is None
    assert "supprimé" in mailer.sent[-1][1]


async def test_usage_lists_pdf_size_and_activity(session, exports):
    lead, project = await _lead_project(session, age_days=10)
    art = (
        await session.execute(select(Article).where(Article.project_id == project.id))
    ).scalar_one()
    session.add(ArticleFile(article_id=art.id, data=b"x" * 1234))
    await session.commit()
    rows = await maintenance.project_usage(session)
    mine = next(r for r in rows if r["id"] == str(project.id))
    assert mine["pdf_bytes"] == 1234 and mine["articles"] == 1 and mine["idle_days"] in (9, 10)
    assert mine["warned_at"] is None


async def test_admin_storage_and_keep_endpoints(
    client, session, admin_user, do_login, exports, monkeypatch
):
    monkeypatch.setattr(get_settings(), "project_retention_days", 30)
    lead, project = await _lead_project(session)
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    st = (await client.get("/api/v1/admin/storage/status")).json()
    assert {"percent", "free", "low"} <= set(st)
    full = (await client.get("/api/v1/admin/storage")).json()
    assert full["retention_days"] == 30 and any(
        p["id"] == str(project.id) for p in full["projects"]
    )
    await maintenance.retention_sweep(session, FakeMailer())
    got = (await client.get(f"/api/v1/projects/{project.id}")).json()
    assert got["retention_delete_on"]  # suppression programmée, visible des responsables
    r = await client.post(f"/api/v1/projects/{project.id}/keep")
    assert r.status_code == 200
    assert (await client.get(f"/api/v1/projects/{project.id}")).json()[
        "retention_delete_on"
    ] is None
    session.expire_all()
    out = await maintenance.retention_sweep(
        session, FakeMailer(), datetime.now(UTC) + timedelta(days=20)
    )
    assert out == {"warned": 0, "deleted": 0}  # « Conserver » vaut activité
