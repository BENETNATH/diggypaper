"""Tests au niveau app : CORS par défaut restreint (F3) + fail-fast secrets (F5)."""

import pytest

from app.core.config import Settings, get_settings, validate_settings
from app.main import create_app


async def test_cors_disabled_by_default(client):
    """F3 : par défaut (cors_origins vide), aucun header Access-Control-Allow-Origin."""
    resp = await client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_validate_settings_rejects_default_secrets():
    """F5 : validate_settings lève RuntimeError sur les secrets de dev par défaut."""
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
    """F5 : des secrets personnalisés passent la validation."""
    settings = Settings(
        jwt_secret="un-secret-personnalise-de-plus-de-32-octets-abcdef",
        anon_salt="un-sel-personnalise",
    )
    validate_settings(settings)  # ne doit pas lever


def test_create_app_rejects_default_secrets(monkeypatch):
    """F5 : create_app() lève RuntimeError si les secrets sont les valeurs de dev."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("ANON_SALT", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        get_settings.cache_clear()
