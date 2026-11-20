"""Sliding session (spec 2026-11-20-sliding-session-401, §1).

B1-B7: the access_token cookie is renewed only when it is valid and less than
10 minutes remain (R1); no set-cookie when the token is missing/invalid/expired
(R2); never a second set-cookie when the response already carries one (R3);
renewed cookie params identical to login (R5).
"""

import re
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

ACCESS_TOKEN_COOKIE = "access_token"


def _set_cookie_values(resp) -> list[str]:
    """Set-Cookie headers carrying the access_token key (raw form)."""
    return [
        h
        for h in resp.headers.get_list("set-cookie")
        if h.split(";", 1)[0].split("=", 1)[0] == ACCESS_TOKEN_COOKIE
    ]


def _token(user, minutes: int, secret: str | None = None) -> str:
    """Access token with exp = now + minutes, built directly (not via the API)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "tv": user.token_version or 0,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm="HS256")


def _plant(client, token: str) -> None:
    client.cookies.delete(ACCESS_TOKEN_COOKIE, domain="app.test")
    client.cookies.set(ACCESS_TOKEN_COOKIE, token, domain="app.test")


async def test_b1_public_request_without_cookie_no_set_cookie(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers


async def test_b2_fresh_token_no_renewal(client, admin_user, do_login):
    """Token fresh (60 min TTL): no set-cookie on the response."""
    resp = await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    assert resp.status_code == 200
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert "set-cookie" not in me.headers


async def test_b3_expiring_token_renewed_with_same_claims(client, admin_user):
    """Token expiring in 5 min: 200 + one renewed cookie, exp ≈ now + 60 min."""
    _plant(client, _token(admin_user, minutes=5))
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    raw = _set_cookie_values(resp)
    assert len(raw) == 1
    new_token = raw[0].split(";", 1)[0].split("=", 1)[1]
    claims = jwt.decode(new_token, get_settings().jwt_secret, algorithms=["HS256"])
    exp = claims["exp"]
    if isinstance(exp, (int, float)):  # pyjwt ≥ 3 returns date claims as numbers
        exp = datetime.fromtimestamp(exp, UTC)
    delta = exp - datetime.now(UTC)
    assert timedelta(minutes=58) <= delta <= timedelta(minutes=62)
    assert claims["sub"] == str(admin_user.id)
    assert claims["email"] == admin_user.email
    assert claims["role"] == "ADMIN"
    assert claims["tv"] == (admin_user.token_version or 0)
    assert "iat" in claims


async def test_b4_expired_token_401_no_renewal(client, admin_user):
    _plant(client, _token(admin_user, minutes=-5))
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


async def test_b5_token_wrong_secret_401_no_renewal(client, admin_user):
    _plant(
        client,
        _token(admin_user, minutes=5, secret="wrong-secret-0123456789abcdef-0123456789abcdef"),
    )
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


async def test_b6_login_exactly_one_set_cookie(client, admin_user, do_login):
    """R3: the middleware must not add a second access_token set-cookie on login."""
    resp = await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    assert resp.status_code == 200
    assert len(_set_cookie_values(resp)) == 1


async def test_b7_renewed_cookie_params_identical_to_login(client, admin_user):
    """R5: read from the raw Set-Cookie header (HttpOnly, SameSite=Strict,
    Path=/, Max-Age = TTL)."""
    _plant(client, _token(admin_user, minutes=5))
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    raw = _set_cookie_values(resp)[0]
    assert "HttpOnly" in raw
    assert re.search(r"SameSite=Strict", raw, re.IGNORECASE)
    assert "Path=/" in raw
    assert "Max-Age=3600" in raw
