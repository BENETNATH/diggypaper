"""Base SQLAlchemy async + engine/session factory."""

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/diggypaper"
)


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DATABASE_URL):
    return create_async_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
