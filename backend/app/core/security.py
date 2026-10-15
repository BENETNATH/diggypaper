"""Mots de passe (argon2-cffi) + JWT (pyjwt HS256).

Access token : claims {sub, email, role, iat, exp}, TTL 1h, stocké en cookie httpOnly.
Token d'invitation : claims {email, purpose="invite", language, iat, exp}, TTL 7 jours.
Pas de refresh token (décision documentée, lot 2).
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

# m3 : hash argon2 factice (module-level) pour égaliser le temps de réponse du login
# quand l'email n'existe pas (évite l'oracle de timing user-existe / user-n'existe-pas).
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
    """Invitation ; avec ``project_id`` + ``role`` : l'inscription rejoint ce projet."""
    settings = get_settings()
    now = _now()
    payload = {
        "email": email,
        "purpose": INVITE_PURPOSE,
        "language": language,
        "iat": now,
        "exp": now + INVITE_TOKEN_TTL,
    }
    if invitation_id:  # lien vers la ligne `invitations` : supprimée = invitation révoquée
        payload["iid"] = invitation_id
    if project_id:
        payload.update(project_id=project_id, role=role or "ANNOTATOR")
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Décode un JWT HS256 ; lève jwt.PyJWTError si invalide/expiré."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def decode_invite_token(token: str) -> dict:
    """Décode un JWT d'invitation ; lève jwt.PyJWTError si expiré ou mauvais purpose."""
    claims = decode_token(token)
    if claims.get("purpose") != INVITE_PURPOSE:
        raise jwt.InvalidTokenError("wrong purpose")
    return claims
