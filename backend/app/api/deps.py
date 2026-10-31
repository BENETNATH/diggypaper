"""FastAPI dependencies: DB session, current user, admin, mailer."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.base import SessionLocal
from app.db.models import User, UserRole
from app.emails import Mailer

ACCESS_TOKEN_COOKIE = "access_token"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_mailer(db: Annotated[AsyncSession, Depends(get_db)]) -> Mailer | None:
    """Mailer (SMTP from the settings, else env); None if sending is not configured."""
    from app.emails import mailer_from_db

    return await mailer_from_db(db)


async def _load_user(request: Request, db: AsyncSession) -> User:
    """Load the user from the access_token cookie (401 if missing/invalid/nonexistent)."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    sub = claims.get("sub")
    if not sub:
        # Ex. an invitation JWT (no sub claim) cannot be used to authenticate.
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token") from None
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if claims.get("tv", 0) != (user.token_version or 0):  # mot de passe changé / déconnecté
        raise HTTPException(status_code=401, detail="Invalid token")
    if user.is_active is False:  # compte désactivé par un admin : session coupée immédiatement
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


async def get_current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> User:
    """Living current user (401 if missing/invalid or anonymized account, F2)."""
    user = await _load_user(request, db)
    if user.anonymized_hash is not None:
        # F2: dead account post-anonymization - no residual access left.
        raise HTTPException(status_code=401, detail="Account anonymized")
    return user


async def get_current_user_or_anonymized(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    """Like get_current_user but tolerates an already-anonymized account.

    Reserved for the anonymization endpoint (idempotence, F2): an already
    anonymized account can re-call DELETE /users/me/anonymize without error.
    """
    return await _load_user(request, db)


def get_client_ip(request: Request) -> str:
    """Client IP (F7/F8, spec \u00a77): first IP of X-Forwarded-For if present (set by
    nginx), else request.client.host. In prod, nginx must set X-Forwarded-For
    reliably (the 1st hop is trusted)."""
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


def rate_limit_user(scope: str, limit: int):
    """Dependency: 429 beyond ``limit`` calls / 15 min per user (not per IP)."""
    from app.api.ratelimit import is_rate_limited, record

    async def dependency(user: Annotated[User, Depends(get_current_user)]) -> None:
        key = f"{scope}:user:{user.id}"
        if is_rate_limited(key, limit):
            raise HTTPException(status_code=429, detail="rate_limited")
        record(key)

    return dependency
