"""Bootstrap du premier admin (idempotent).

Au démarrage, si ``ADMIN_EMAIL``/``ADMIN_PASSWORD`` sont définis et qu'aucun admin
n'existe, le compte admin est créé (ou l'utilisateur existant est promu). C'est le
seul mécanisme pour démarrer l'application à l'état vide : sans admin, on ne peut
ni inviter ni configurer.
"""

import logging

from sqlalchemy import select

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import SessionLocal
from app.db.models import User, UserRole

logger = logging.getLogger(__name__)


async def bootstrap_admin(settings: Settings) -> None:
    """Créer/promouvoir le premier admin si les vars sont définies et qu'aucun admin n'existe."""
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
