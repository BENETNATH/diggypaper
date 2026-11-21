"""Tests for the personal API key (spec 2026-11-21): generate/revoke + header auth.

The key is `dgk_` + 32 urlsafe chars; only its sha256 is stored. The header
X-Api-Key authenticates when the cookie is absent or invalid; a valid cookie
always wins.
"""

import hashlib
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import User

DGK_RE = re.compile(r"^dgk_[A-Za-z0-9_-]{32}$")


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def _set_key_in_db(session, email: str, key: str) -> None:
    """Seed a known key (hash only) for the header-auth tests."""
    user = (await session.execute(select(User).where(User.email == email))).scalar_one()
    user.api_key_hash = _digest(key)
    user.api_key_created_at = datetime.now(UTC)
    await session.commit()


# --- R2: POST /me/api-key ---


async def test_post_api_key_201_format_and_me_flags(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/auth/me/api-key")
    assert resp.status_code == 201
    key = resp.json()["api_key"]
    assert DGK_RE.match(key)
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    data = me.json()
    assert data["api_key_set"] is True
    assert data["api_key_created_at"] is not None
    # The plaintext key exists ONLY in the 201 body, never in /me.
    assert key not in me.text
    user = (
        await session.execute(select(User).where(User.email == "researcher@lab.fr"))
    ).scalar_one()
    assert user.api_key_hash == _digest(key)
    assert user.api_key_hash != key
    assert user.api_key_created_at is not None


async def test_post_api_key_existing_409(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.post("/api/v1/auth/me/api-key")).status_code == 201
    assert (await client.post("/api/v1/auth/me/api-key")).status_code == 409


# --- R3: DELETE /me/api-key ---


async def test_delete_api_key_204_then_404(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.post("/api/v1/auth/me/api-key")).status_code == 201
    assert (await client.delete("/api/v1/auth/me/api-key")).status_code == 204
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["api_key_set"] is False
    assert me.json()["api_key_created_at"] is None
    assert (await client.delete("/api/v1/auth/me/api-key")).status_code == 404


# --- R5: header auth ---


async def test_header_auth_valid_key_no_cookie_200(client, session, researcher_user):
    key = "dgk_" + "b" * 32
    await _set_key_in_db(session, "researcher@lab.fr", key)
    resp = await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})
    assert resp.status_code == 200
    assert resp.json()["email"] == "researcher@lab.fr"


async def test_header_auth_wrong_key_401(client, researcher_user):
    resp = await client.get("/api/v1/auth/me", headers={"X-Api-Key": "dgk_" + "x" * 32})
    assert resp.status_code == 401


async def test_no_cookie_no_header_401(client, researcher_user):
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_valid_cookie_beats_bad_header_200(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get("/api/v1/auth/me", headers={"X-Api-Key": "dgk_" + "x" * 32})
    assert resp.status_code == 200


async def test_invalid_cookie_falls_back_to_valid_key_200(client, session, researcher_user):
    key = "dgk_" + "e" * 32
    await _set_key_in_db(session, "researcher@lab.fr", key)
    client.cookies.set("access_token", "garbage-token")
    resp = await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})
    assert resp.status_code == 200


async def test_stale_cookie_falls_back_to_valid_key_200(client, session, researcher_user):
    """A validly-signed cookie revoked by a token_version bump must not block
    the header fallback (the central case of the _load_user refactor)."""
    from app.core.security import create_access_token

    key = "dgk_" + "f" * 32
    await _set_key_in_db(session, "researcher@lab.fr", key)
    user = (
        await session.execute(select(User).where(User.email == "researcher@lab.fr"))
    ).scalar_one()
    token = create_access_token(user)
    user.token_version = (user.token_version or 0) + 1  # revokes the token
    await session.commit()
    client.cookies.set("access_token", token)
    resp = await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})
    assert resp.status_code == 200
    assert resp.json()["email"] == "researcher@lab.fr"


async def test_header_auth_disabled_account_401(client, session, researcher_user):
    key = "dgk_" + "c" * 32
    await _set_key_in_db(session, "researcher@lab.fr", key)
    user = (
        await session.execute(select(User).where(User.email == "researcher@lab.fr"))
    ).scalar_one()
    user.is_active = False
    await session.commit()
    assert (await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})).status_code == 401


async def test_header_auth_anonymized_account_401(client, session, researcher_user):
    key = "dgk_" + "d" * 32
    await _set_key_in_db(session, "researcher@lab.fr", key)
    user = (
        await session.execute(select(User).where(User.email == "researcher@lab.fr"))
    ).scalar_one()
    user.anonymized_hash = "z" * 64
    await session.commit()
    assert (await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})).status_code == 401


async def test_anonymize_nulls_api_key(client, session, researcher_user, do_login):
    """Audit 2026-11-21: anonymization erases the personal API key (no usable
    credential material on a dead account)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    key = (await client.post("/api/v1/auth/me/api-key")).json()["api_key"]
    assert (await client.delete("/api/v1/users/me/anonymize")).status_code == 204
    user = (
        await session.execute(select(User).where(User.anonymized_hash != None))  # noqa: E711
    ).scalar_one()
    assert user.api_key_hash is None
    assert user.api_key_created_at is None
    assert (await client.get("/api/v1/auth/me", headers={"X-Api-Key": key})).status_code == 401
