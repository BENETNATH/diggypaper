"""App-level tests: restrictive default CORS (F3) + fail-fast secrets (F5)."""

import pytest

from app.core.config import Settings, get_settings, validate_settings
from app.main import create_app


async def test_cors_disabled_by_default(client):
    """F3: by default (empty cors_origins), no Access-Control-Allow-Origin header."""
    resp = await client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_validate_settings_rejects_default_secrets():
    """F5: validate_settings raises RuntimeError on the default dev secrets."""
    settings = Settings(
        jwt_secret="dev-secret-change-me-0123456789abcdef",
        anon_salt="diggypaper-anon",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_settings(settings)
    msg = str(exc.value)
    assert "JWT_SECRET" in msg
    assert "ANON_SALT" in msg


def test_validate_settings_accepts_custom_secrets():
    """F5: custom secrets pass validation."""
    settings = Settings(
        jwt_secret="un-secret-personnalise-de-plus-de-32-octets-abcdef",
        anon_salt="un-sel-personnalise",
    )
    validate_settings(settings)  # ne doit pas lever


def test_create_app_rejects_default_secrets(monkeypatch):
    """F5: create_app() raises RuntimeError if the secrets are the dev values."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("ANON_SALT", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        get_settings.cache_clear()
