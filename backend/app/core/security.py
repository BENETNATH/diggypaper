"""Passwords (argon2-cffi) + JWT (pyjwt HS256).

Access token: claims {sub, email, role, iat, exp}, TTL 1 h, stored in an httpOnly cookie.
Invitation token: claims {email, purpose="invite", language, iat, exp}, TTL 7 days.
No refresh token (documented decision, lot 2).
"""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from app.core.config import get_settings
from app.db.models import User

INVITE_PURPOSE = "invite"
INVITE_TOKEN_TTL = timedelta(days=7)

_password_hasher = PasswordHasher()

# m3: dummy argon2 hash (module-level) to equalize the login response time when
# the email does not exist (avoids the user-exists / user-does-not-exist timing oracle).
DUMMY_HASH = _password_hasher.hash("timing-equalization-dummy-password")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return _password_hasher.verify(hashed, password)
    except Argon2Error:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value if user.role is not None else None,
        "tv": user.token_version or 0,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_invite_token(
    email: str,
    language: str = "en",
    project_id: str | None = None,
    role: str | None = None,
    invitation_id: str | None = None,
) -> str:
    """Invitation; with ``project_id`` + ``role``: registration joins that project."""
    settings = get_settings()
    now = _now()
    payload = {
        "email": email,
        "purpose": INVITE_PURPOSE,
        "language": language,
        "iat": now,
        "exp": now + INVITE_TOKEN_TTL,
    }
    if invitation_id:  # link to the `invitations` row: deleted = invitation revoked
        payload["iid"] = invitation_id
    if project_id:
        payload.update(project_id=project_id, role=role or "ANNOTATOR")
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decodes an HS256 JWT; raises jwt.PyJWTError if invalid/expired."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def decode_invite_token(token: str) -> dict:
    """Decodes an invitation JWT; raises jwt.PyJWTError if expired or wrong purpose."""
    claims = decode_token(token)
    if claims.get("purpose") != INVITE_PURPOSE:
        raise jwt.InvalidTokenError("wrong purpose")
    return claims
