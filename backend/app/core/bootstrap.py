"""Bootstrap of the first admin (idempotent).

At startup, if ``ADMIN_EMAIL``/``ADMIN_PASSWORD`` are defined and no admin
exists, the admin account is created (or the existing user is promoted). This
is the only way to start the application in a fresh state: without an admin
you can neither invite nor configure.
"""

import logging

from sqlalchemy import select

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import SessionLocal
from app.db.models import User, UserRole

logger = logging.getLogger(__name__)


async def bootstrap_admin(settings: Settings) -> None:
    """Create/promote the first admin if the vars are defined and no admin exists."""
    if not settings.admin_email or not settings.admin_password:
        return
    async with SessionLocal() as session:
        existing_admin = (
            await session.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
        ).scalar_one_or_none()
        if existing_admin is not None:
            return
        existing_user = (
            await session.execute(select(User).where(User.email == settings.admin_email))
        ).scalar_one_or_none()
        if existing_user is not None:
            existing_user.role = UserRole.ADMIN
            logger.info("Bootstrap : compte ADMIN_EMAIL promu admin")
        else:
            session.add(
                User(
                    email=settings.admin_email,
                    hashed_password=hash_password(settings.admin_password),
                    role=UserRole.ADMIN,
                )
            )
            logger.info("Bootstrap : création du premier admin (ADMIN_EMAIL)")
        await session.commit()
