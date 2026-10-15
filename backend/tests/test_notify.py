"""Tests de la notification post-job (app/tasks/notify.py) — 100 % offline."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Project, User, UserRole
from app.tasks.notify import _format_stats, notify_job_complete


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail = False

    async def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append({"to": to, "subject": subject, "body": body})


async def _make_lead(session: AsyncSession, lang: str | None = None) -> User:
    from app.core.security import hash_password

    user = User(
        email="lead@lab.fr",
        hashed_password=hash_password("lead-pass-123"),
        role=UserRole.RESEARCHER,
        language_preference=lang,
        data_consent_given=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _make_project(session: AsyncSession, lead: User | None) -> Project:
    project = Project(title="Projet Test", created_by=lead.id if lead else None)
    session.add(project)
    await session.commit()
    return project


# --- _format_stats ---


def test_format_stats_benchmark():
    stats = {
        "models": 2,
        "articles": 10,
        "results": [
            {"model_name": "A", "f1_score": 0.9, "accuracy": 0.8, "execution_time_seconds": 1.5},
            {"model_name": "B", "f1_score": 0.7, "accuracy": 0.6, "execution_time_seconds": 2.0},
        ],
    }
    text = _format_stats("benchmark", stats)
    assert "Modèles testés: 2, articles: 10" in text
    assert "A: F1=0.90" in text
    assert "B: F1=0.70" in text


def test_format_stats_import():
    text = _format_stats("import", {"imported": 5, "failed": 1})
    assert "Importés: 5, échecs: 1" in text


def test_format_stats_import_truncated():
    stats = {"imported": 10000, "failed": 0, "truncated": True, "total_count": 12345}
    text = _format_stats("import", stats)
    assert "tronqué: count réel 12345" in text


def test_format_stats_unknown():
    text = _format_stats("autre", {"x": 1})
    assert "{'x': 1}" in text


def test_format_stats_benchmark_empty():
    """Stats vides → compteurs à 0 (le branch benchmark produit toujours une ligne)."""
    assert _format_stats("benchmark", {}) == "Modèles testés: 0, articles: 0"


# --- notify_job_complete ---


async def test_notify_sends_to_lead_fr(engine, session):
    lead = await _make_lead(session, lang="fr")
    project = await _make_project(session, lead)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mailer = FakeMailer()
    await notify_job_complete(
        str(project.id),
        "benchmark",
        {"models": 1, "articles": 1, "results": []},
        session_factory=factory,
        mailer=mailer,
        app_base_url="https://app.test",
    )
    assert len(mailer.sent) == 1
    msg = mailer.sent[0]
    assert msg["to"] == "lead@lab.fr"
    assert "terminé" in msg["subject"]  # fr
    assert "Projet Test" in msg["body"]
    assert "https://app.test/projects/" in msg["body"]


async def test_notify_sends_to_lead_en(engine, session):
    lead = await _make_lead(session, lang="en")
    project = await _make_project(session, lead)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mailer = FakeMailer()
    await notify_job_complete(
        str(project.id),
        "import",
        {"imported": 3, "failed": 0},
        session_factory=factory,
        mailer=mailer,
    )
    assert len(mailer.sent) == 1
    assert "finished" in mailer.sent[0]["subject"]  # en


async def test_notify_no_lead_no_send(engine, session):
    """Projet sans created_by → pas d'e-mail, pas d'exception."""
    project = await _make_project(session, None)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mailer = FakeMailer()
    await notify_job_complete(
        str(project.id), "import", {"imported": 1}, session_factory=factory, mailer=mailer
    )
    assert mailer.sent == []


async def test_notify_project_not_found(engine, session):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mailer = FakeMailer()
    await notify_job_complete(
        str(uuid.uuid4()), "import", {}, session_factory=factory, mailer=mailer
    )
    assert mailer.sent == []


async def test_notify_mailing_failure_never_raises(engine, session):
    """Échec SMTP → loggé, pas d'exception (best-effort)."""
    lead = await _make_lead(session, lang="en")
    project = await _make_project(session, lead)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mailer = FakeMailer()
    mailer.fail = True
    await notify_job_complete(
        str(project.id), "import", {"imported": 1}, session_factory=factory, mailer=mailer
    )
    assert mailer.sent == []


async def test_notify_no_mailer_skips(engine, session):
    """mailer=None → pas d'envoi (le caller fournit son mailer)."""
    lead = await _make_lead(session, lang="en")
    project = await _make_project(session, lead)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await notify_job_complete(
        str(project.id), "import", {"imported": 1}, session_factory=factory, mailer=None
    )
    # pas d'exception, rien à vérifier (pas de mailer)
