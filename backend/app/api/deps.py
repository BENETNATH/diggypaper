"""Dépendances FastAPI : session DB, user courant, admin, mailer."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_token
from app.db.base import SessionLocal
from app.db.models import User, UserRole
from app.emails import Mailer

ACCESS_TOKEN_COOKIE = "access_token"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_mailer() -> Mailer:
    return Mailer(get_settings())


async def _load_user(request: Request, db: AsyncSession) -> User:
    """Charge le user depuis le cookie access_token (401 si absent/invalide/inexistant)."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    sub = claims.get("sub")
    if not sub:
        # Ex. un JWT d'invitation (pas de claim sub) ne sert pas à s'authentifier.
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token") from None
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if user.is_active is False:  # compte désactivé par un admin : session coupée immédiatement
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


async def get_current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> User:
    """User courant vivant (401 si absent/invalide ou compte anonymisé, F2)."""
    user = await _load_user(request, db)
    if user.anonymized_hash is not None:
        # F2 : compte mort post-anonymisation — plus aucun accès résiduel.
        raise HTTPException(status_code=401, detail="Account anonymized")
    return user


async def get_current_user_or_anonymized(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    """Comme get_current_user mais tolère un compte déjà anonymisé.

    Réservé à l'endpoint d'anonymisation (idempotence, F2) : un compte déjà
    anonymisé peut re-appeler DELETE /users/me/anonymize sans erreur.
    """
    return await _load_user(request, db)


def get_client_ip(request: Request) -> str:
    """IP client (F7/F8, spec §7) : premier IP de X-Forwarded-For si présent (posé par
    nginx), sinon request.client.host. En prod, nginx doit poser X-Forwarded-For de
    façon fiable (le 1er saut est fait confiance)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return "unknown"


async def get_current_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin required")
    return user
