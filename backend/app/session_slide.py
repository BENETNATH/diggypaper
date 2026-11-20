"""Sliding session (spec 2026-11-20-sliding-session-401, §1).

Renews the access_token cookie while the user stays active: a valid cookie
with less than SESSION_SLIDE_THRESHOLD_MINUTES left is re-issued on the
response (same claims — sub, email, role, tv — with fresh iat/exp).
No DB access, no logging (R4): the renewal is best-effort and never
alters the response on error.
"""

from datetime import UTC, datetime, timedelta

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.deps import ACCESS_TOKEN_COOKIE
from app.core.config import Settings, get_settings

SESSION_SLIDE_THRESHOLD_MINUTES = 10


def renewed_access_token(token: str, settings: Settings) -> str | None:
    """A renewed token if the cookie is valid with < threshold left, else None (R1/R2)."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if not claims.get("sub"):
        # R1's claims are sub/email/role/tv: an invitation JWT (no sub) planted
        # in the cookie is not an access token and must not slide.
        return None
    exp = claims.get("exp")
    if exp is None:
        return None
    if isinstance(exp, (int, float)):  # pyjwt ≥ 3 returns date claims as numbers
        exp = datetime.fromtimestamp(exp, UTC)
    now = datetime.now(UTC)
    if exp - now >= timedelta(minutes=SESSION_SLIDE_THRESHOLD_MINUTES):
        return None
    payload = {k: v for k, v in claims.items() if k not in ("iat", "exp")}
    payload["iat"] = now
    payload["exp"] = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


class SessionSlideMiddleware(BaseHTTPMiddleware):
    """Re-issues the access_token cookie on the response when it is about to
    expire (R1, R5). Silent; never adds a second cookie when the response
    already carries one (R3); any error leaves the response untouched (R4)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        try:
            token = request.cookies.get(ACCESS_TOKEN_COOKIE)
            if token is None:
                return response
            renewed = renewed_access_token(token, get_settings())
            if renewed is None:
                return response
            for header in response.headers.getlist("set-cookie"):
                if header.split(";", 1)[0].split("=", 1)[0] == ACCESS_TOKEN_COOKIE:
                    return response  # R3: the response's own cookie wins
            settings = get_settings()
            response.set_cookie(
                key=ACCESS_TOKEN_COOKIE,
                value=renewed,
                max_age=settings.access_token_expire_minutes * 60,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="strict",
                path="/",
            )
        except Exception:  # noqa: BLE001 — best-effort renewal, never break the response
            pass
        return response
