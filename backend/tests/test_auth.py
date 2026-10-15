"""Tests du flux d'authentification : invite → register → login → me + sécurité."""

import re
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import User
from app.i18n import t


def _token_from_email(mock_mailer) -> str:
    body = mock_mailer.sent[-1]["body"]
    line = next(line for line in body.splitlines() if "/register?token=" in line).strip()
    return line.split("token=", 1)[1]


async def _invite(client, do_login, email: str, language: str | None = None) -> None:
    await do_login("admin@lab.fr", "admin-pass-123")
    payload: dict[str, str] = {"email": email}
    if language is not None:
        payload["language"] = language
    resp = await client.post("/api/v1/auth/invite", json=payload)
    assert resp.status_code == 202


def _forge_invite_claims(email: str, **extra) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "email": email,
        "purpose": "invite",
        "language": "en",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(extra)
    return pyjwt.encode(claims, settings.jwt_secret, algorithm="HS256")


# --- invite ---


async def test_invite_admin_ok_email_captured(client, admin_user, mock_mailer, do_login):
    await _invite(client, do_login, "newbie@lab.fr")
    assert len(mock_mailer.sent) == 1
    msg = mock_mailer.sent[0]
    assert msg["to"] == "newbie@lab.fr"
    assert msg["subject"] == t("en", "invite_subject")
    assert "/register?token=" in msg["body"]


async def test_invite_non_admin_forbidden(client, researcher_user, mock_mailer, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/auth/invite", json={"email": "x@lab.fr"})
    assert resp.status_code == 403
    assert mock_mailer.sent == []


async def test_invite_requires_auth(client, mock_mailer):
    resp = await client.post("/api/v1/auth/invite", json={"email": "x@lab.fr"})
    assert resp.status_code == 401
    assert mock_mailer.sent == []


# --- register ---


async def test_register_valid_token_creates_user(client, admin_user, mock_mailer, do_login):
    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "newbie@lab.fr"
    assert data["role"] == "RESEARCHER"
    assert data["language_preference"] == "en"
    assert "hashed_password" not in data


async def test_register_inherits_invite_language(client, admin_user, mock_mailer, do_login):
    await _invite(client, do_login, "newbie@lab.fr", language="fr")
    token = _token_from_email(mock_mailer)
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 201
    assert resp.json()["language_preference"] == "fr"


async def test_register_expired_token_rejected(client):
    token = _forge_invite_claims("newbie@lab.fr", exp=datetime.now(UTC) - timedelta(hours=1))
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 401


async def test_register_wrong_purpose_rejected(client):
    token = _forge_invite_claims("newbie@lab.fr", purpose="login")
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 401


async def test_register_short_password_422(client, admin_user, mock_mailer, do_login):
    """F6 : password < 8 caractères → 422 (politique de mot de passe)."""
    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    resp = await client.post("/api/v1/auth/register", json={"token": token, "password": "a"})
    assert resp.status_code == 422


async def test_register_min_length_password_ok(client, admin_user, mock_mailer, do_login):
    """F6 : password de 8 caractères → 201."""
    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    resp = await client.post("/api/v1/auth/register", json={"token": token, "password": "12345678"})
    assert resp.status_code == 201


async def test_register_duplicate_token_409(client, admin_user, mock_mailer, do_login):
    """m1 : 2e register avec le même token → 409 'User already exists' (pas de 500)."""
    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    resp1 = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp1.status_code == 201
    resp2 = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp2.status_code == 409
    assert resp2.json()["detail"] == "User already exists"


async def test_register_integrity_error_catcher(session):
    """m1 : le catcher IntegrityError (TOCTOU sur commit) renvoie 409, pas 500."""
    from fastapi import HTTPException

    from app.api.v1.auth import _commit_user

    # User déjà en base → le commit du doublon viole la contrainte unique.
    session.add(User(email="dup@lab.fr", hashed_password="h"))
    await session.commit()
    session.add(User(email="dup@lab.fr", hashed_password="h2"))
    with pytest.raises(HTTPException) as exc:
        await _commit_user(session)
    assert exc.value.status_code == 409
    assert exc.value.detail == "User already exists"


# --- login ---


async def test_login_ok_sets_secure_httponly_cookie(client, researcher_user, do_login):
    resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "researcher@lab.fr"
    set_cookie = resp.headers.get("set-cookie", "")
    assert set_cookie.startswith("access_token=")
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    # Valeur SameSite insensible à la casse (RFC 6265bis) ; starlette écrit "strict".
    assert re.search(r"SameSite=Strict", set_cookie, re.IGNORECASE)
    assert "Path=/" in set_cookie


async def test_login_wrong_password_401(client, researcher_user, do_login):
    resp = await do_login("researcher@lab.fr", "wrong-password")
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


async def test_login_unknown_email_401(client, do_login):
    resp = await do_login("ghost@lab.fr", "whatever")
    assert resp.status_code == 401


async def test_login_first_time_without_consent_403(client, researcher_user, do_login):
    resp = await do_login("researcher@lab.fr", "researcher-pass-123")
    assert resp.status_code == 403
    assert resp.json()["detail"] == t("en", "consent_required")
    assert "set-cookie" not in resp.headers


async def test_login_with_consent_stores_consent(client, session, researcher_user, do_login):
    resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert resp.status_code == 200
    user = (
        await session.execute(select(User).where(User.email == "researcher@lab.fr"))
    ).scalar_one()
    assert user.data_consent_given is True


# --- me ---


async def test_me_authenticated_returns_profile(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "researcher@lab.fr"
    assert data["role"] == "RESEARCHER"
    assert "hashed_password" not in data


async def test_me_unauthenticated_401(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# --- sécurité ---


async def test_hashed_password_never_in_any_response(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    for path in ("/api/v1/auth/me", "/api/v1/users/me/export-data"):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert "hashed_password" not in resp.text
        assert "researcher-pass-123" not in resp.text


async def test_invite_token_cannot_authenticate(client, admin_user, mock_mailer, do_login):
    """Le JWT d'invitation (pas de claim sub) ne sert pas à s'authentifier."""
    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    client.cookies.delete("access_token", domain="app.test")
    client.cookies.set("access_token", token, domain="app.test")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_profile_update_password_and_logout(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    r = await client.patch(
        "/api/v1/auth/me", json={"full_name": " Ada ", "language_preference": "de"}
    )
    assert (
        r.status_code == 200
        and r.json()["full_name"] == "Ada"
        and r.json()["language_preference"] == "de"
    )
    assert (
        await client.patch("/api/v1/auth/me", json={"language_preference": "xx"})
    ).status_code == 422
    bad = {"current_password": "nope", "new_password": "new-pass-1234"}
    assert (await client.post("/api/v1/auth/change-password", json=bad)).status_code == 403
    short = {"current_password": "researcher-pass-123", "new_password": "short"}
    assert (await client.post("/api/v1/auth/change-password", json=short)).status_code == 422
    ok = {"current_password": "researcher-pass-123", "new_password": "new-pass-1234"}
    assert (await client.post("/api/v1/auth/change-password", json=ok)).status_code == 204
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
