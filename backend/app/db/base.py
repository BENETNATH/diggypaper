"""Base SQLAlchemy async + engine/session factory."""

import os
import unicodedata

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/diggypaper"
)


class Base(DeclarativeBase):
    pass


def _unaccent(text):
    """Sans accents (SQLite des tests) : l'équivalent de ``immutable_unaccent`` de PostgreSQL."""
    if text is None:
        return None
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


@event.listens_for(Engine, "connect")
def _register_sqlite_functions(dbapi_connection, _record) -> None:
    # La recherche rapide utilise ``immutable_unaccent`` (fonction PostgreSQL, migration 0028) ;
    # SQLite (tests) reçoit la même fonction en Python.
    create = getattr(dbapi_connection, "create_function", None)
    if create is not None:
        create("immutable_unaccent", 1, _unaccent, deterministic=True)


def make_engine(url: str = DATABASE_URL):
    return create_async_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
