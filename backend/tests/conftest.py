"""Fixtures partagées : engine SQLite async in-memory + session + client API."""

import os
from pathlib import Path

# Settings lit "./.env" relatif au CWD : lancé depuis la racine du repo, pytest
# chargerait le .env de production (secrets réels, IMPORT_MAX_ARTICLES, …) et
# ferait échouer ~12 tests. On se place dans backend/ — le CWD documenté du README.
os.chdir(Path(__file__).resolve().parents[1])

# F5 : secrets de test AVANT tout import de app (create_app() fait fail-fast sur les
# valeurs de dev par défaut). JWT_SECRET ≥ 32 octets (HS256).
os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef-0123456789abcdef"
os.environ["ANON_SALT"] = "test-anon-salt"
# Clé maîtresse AES-256 (Module 7) : 64 hex chars = 32 octets.
os.environ["ENCRYPTION_KEY"] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
# Celery en mode eager (exécution inline, sans broker Redis) — lot 4A.
os.environ["CELERY_EAGER"] = "true"
os.environ["COOKIE_SECURE"] = "false"  # client de test en HTTP ; défaut applicatif : true

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.ratelimit import reset as _reset_ratelimit
from app.db.base import Base
from app.db.models import User, UserRole


@pytest.fixture(autouse=True)
def _clean_ratelimit():
    """F7 : le limiteur est in-memory et global → on le vide avant/après chaque test."""
    _reset_ratelimit()
    yield
    _reset_ratelimit()


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLite n'applique les FK (CASCADE / SET NULL) que si le pragma est activé.
    @event.listens_for(eng.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


class MockMailer:
    """Mailer de test : capture les messages au lieu d'envoyer du SMTP réel."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


@pytest.fixture
def mock_mailer() -> MockMailer:
    return MockMailer()


class FakeRedis:
    """Redis en mémoire pour les tests du lock par modèle : SET NX / GET / DEL / EXPIRE.

    ``set_calls`` : [(key, value, nx, ex)] ; ``expire_calls`` : [(key, seconds)] ;
    ``events`` : journal d'ordonnancement.
    """

    def __init__(self) -> None:
        self.store: dict = {}
        self.set_calls: list[tuple] = []
        self.expire_calls: list[tuple] = []
        self.events: list[str] = []

    async def expire(self, key, seconds):
        self.expire_calls.append((key, seconds))
        self.events.append("expire")
        return key in self.store

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        self.events.append("set")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
async def client(engine, session, mock_mailer):
    """Client API : app réelle, DB in-memory, mailer mocké (100 % offline)."""
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db, get_mailer
    from app.main import create_app

    app = create_app()

    async def _override_get_db() -> AsyncSession:
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_mailer] = lambda: mock_mailer

    # https:// : le déploiement a du TLS en amont (spec §7) ; le cookie Secure ne
    # round-trippe que sur un origine sécurisé. Host avec un point : le cookie jar
    # standard (http.cookiejar) traite les hosts sans point comme "local" (domain
    # dérivé en "<host>.local"), ce qui casserait le round-trip du cookie.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://app.test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _make_user(
    session: AsyncSession,
    email: str,
    role: UserRole,
    password: str,
    consent: bool = False,
    creator: bool = True,
) -> User:
    from app.core.security import hash_password

    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role,
        data_consent_given=consent,
        can_create_projects=creator,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def admin_user(session) -> User:
    """Admin seedé directement en base (pas via API), consentement déjà donné."""
    return await _make_user(session, "admin@lab.fr", UserRole.ADMIN, "admin-pass-123", consent=True)


@pytest.fixture
async def researcher_user(session) -> User:
    """Researcher seedé en base (pas via API), sans consentement (tests RGPD)."""
    return await _make_user(
        session, "researcher@lab.fr", UserRole.RESEARCHER, "researcher-pass-123"
    )


@pytest.fixture
def do_login(client):
    """Aide : POST /api/v1/auth/login, renvoie la réponse."""

    async def _login(email: str, password: str, consent: bool | None = None):
        payload: dict[str, object] = {"email": email, "password": password}
        if consent is not None:
            payload["consent"] = consent
        return await client.post("/api/v1/auth/login", json=payload)

    return _login
