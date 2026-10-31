"""Tests for the password reset flow: request -> e-mail -> confirm."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import PasswordReset, User


def _reset_token_from_email(mock_mailer) -> str:
    body = mock_mailer.sent[-1]["body"]
    line = next(line for line in body.splitlines() if "/reset?token=" in line).strip()
    return line.split("token=", 1)[1]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _request(client, email: str) -> "object":
    return await client.post("/api/v1/auth/password-reset/request", json={"email": email})


async def _confirm(client, token: str, password: str = "new-pass-1234") -> "object":
    return await client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": password}
    )


# --- request ---


async def test_reset_request_unknown_email_200_generic_no_token(client, session, mock_mailer):
    """No user: 200 with the same generic body as the existing-user case, no row, no e-mail."""
    r1 = await _request(client, "ghost@lab.fr")
    assert r1.status_code == 200
    assert mock_mailer.sent == []
    assert (await session.execute(select(PasswordReset))).scalars().all() == []
    r2 = await _request(client, "another@ghost.fr")
    assert r2.status_code == 200
    assert r2.json() == r1.json()


async def test_reset_request_disabled_user_no_email(client, session, mock_mailer):
    user = User(email="inactive@lab.fr", hashed_password="h", is_active=False)
    session.add(user)
    await session.commit()
    resp = await _request(client, "inactive@lab.fr")
    assert resp.status_code == 200
    assert mock_mailer.sent == []


async def test_reset_request_active_user_mail_captured_token_hashed(
    client, session, researcher_user, mock_mailer
):
    """Active user: e-mail sent, token stored as SHA-256 hash (raw token never persisted)."""
    resp = await _request(client, "Researcher@lab.fr")  # case-insensitive, like login
    assert resp.status_code == 200
    assert len(mock_mailer.sent) == 1
    assert mock_mailer.sent[0]["to"] == "researcher@lab.fr"
    token = _reset_token_from_email(mock_mailer)
    row = (await session.execute(select(PasswordReset))).scalar_one()
    assert row.user_id == researcher_user.id
    assert row.token_hash == _digest(token)
    assert mock_mailer.sent[0]["body"].count(token) == 1  # raw token only in the link
    assert row.consumed_at is None
    exp = row.expires_at
    if exp.tzinfo is None:  # SQLite returns naive datetimes
        exp = exp.replace(tzinfo=UTC)
    assert exp > datetime.now(UTC)


# --- confirm ---


async def test_reset_confirm_valid_token_changes_password_and_consumes(
    client, session, researcher_user, mock_mailer, do_login
):
    await _request(client, "researcher@lab.fr")
    token = _reset_token_from_email(mock_mailer)
    resp = await _confirm(client, token)
    assert resp.status_code == 200
    # old password dead, new one works; other sessions revoked (token_version).
    assert (await do_login("researcher@lab.fr", "researcher-pass-123")).status_code == 401
    assert (await do_login("researcher@lab.fr", "new-pass-1234", consent=True)).status_code == 200
    row = (await session.execute(select(PasswordReset))).scalar_one()
    assert row.consumed_at is not None
    # second confirm with the same token fails.
    resp2 = await _confirm(client, token)
    assert resp2.status_code == 404
    assert resp2.json()["detail"] == "invalid_token"


async def test_reset_confirm_unknown_token_404(client, researcher_user):
    resp = await _confirm(client, "not-a-real-token")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "invalid_token"


async def test_reset_confirm_expired_token_400(client, session, researcher_user):
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            user_id=researcher_user.id,
            token_hash=_digest(token),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await session.commit()
    resp = await _confirm(client, token)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "expired_token"


async def test_reset_confirm_consumed_token_404(client, session, researcher_user):
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(
        PasswordReset(
            user_id=researcher_user.id,
            token_hash=_digest(token),
            expires_at=now + timedelta(hours=1),
            consumed_at=now,
        )
    )
    await session.commit()
    resp = await _confirm(client, token)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "invalid_token"


async def test_reset_confirm_short_password_422(client, researcher_user):
    """Same password policy as register: under 8 characters -> 422."""
    resp = await _confirm(client, "whatever", password="short")
    assert resp.status_code == 422
