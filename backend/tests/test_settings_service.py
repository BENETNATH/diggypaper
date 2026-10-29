"""Tests du service settings (app/settings/service.py, Module 7)."""

import pytest
from sqlalchemy import func, select

from app.core.crypto import MASK
from app.db.models import Project, SettingScope, SystemSettings, User
from app.settings.service import delete_setting, get_all, get_setting, set_setting


async def test_set_get_secret_encrypted(session):
    """Secret chiffré en base : le plaintext n'apparaît PAS en clair dans value_encrypted."""
    await set_setting(session, "ncbi_api_key", "my-secret-ncbi-key")
    assert await get_setting(session, "ncbi_api_key") == "my-secret-ncbi-key"
    row = (
        await session.execute(
            select(SystemSettings).where(SystemSettings.key_name == "ncbi_api_key")
        )
    ).scalar_one()
    assert row.is_secret is True
    assert b"my-secret-ncbi-key" not in row.value_encrypted


async def test_set_get_non_secret_plaintext(session):
    """Valeur non sensible : stockée en clair (UTF-8) dans value_encrypted."""
    await set_setting(session, "smtp_host", "mail.example.com")
    assert await get_setting(session, "smtp_host") == "mail.example.com"
    row = (
        await session.execute(select(SystemSettings).where(SystemSettings.key_name == "smtp_host"))
    ).scalar_one()
    assert row.is_secret is False
    assert row.value_encrypted == b"mail.example.com"


async def test_default_llm_api_base_url(session):
    assert await get_setting(session, "llm_api_base_url") == "http://localhost:11434/v1"


async def test_delete_reverts_default(session):
    await set_setting(session, "llm_api_base_url", "http://custom:1234/v1")
    assert await get_setting(session, "llm_api_base_url") == "http://custom:1234/v1"
    await delete_setting(session, "llm_api_base_url")
    assert await get_setting(session, "llm_api_base_url") == "http://localhost:11434/v1"


async def test_upsert_no_duplicate(session):
    """Upsert : 2 set_setting sur la même clé → 1 seule ligne (index uniques partiels)."""
    await set_setting(session, "ncbi_api_key", "key-1")
    await set_setting(session, "ncbi_api_key", "key-2")
    count = (
        await session.execute(
            select(func.count())
            .select_from(SystemSettings)
            .where(SystemSettings.key_name == "ncbi_api_key")
        )
    ).scalar_one()
    assert count == 1
    assert await get_setting(session, "ncbi_api_key") == "key-2"


async def test_scopes_isolated(session):
    """GLOBAL / PROJECT / USER sont isolés pour la même clé."""
    project = Project(title="P")
    user = User(email="scope@test.fr", hashed_password="h")
    session.add_all([project, user])
    await session.commit()

    await set_setting(session, "ncbi_api_key", "global-key", scope=SettingScope.GLOBAL)
    await set_setting(
        session, "ncbi_api_key", "project-key", scope=SettingScope.PROJECT, project_id=project.id
    )
    await set_setting(session, "ncbi_api_key", "user-key", scope=SettingScope.USER, user_id=user.id)

    assert await get_setting(session, "ncbi_api_key", scope=SettingScope.GLOBAL) == "global-key"
    assert (
        await get_setting(
            session, "ncbi_api_key", scope=SettingScope.PROJECT, project_id=project.id
        )
        == "project-key"
    )
    assert (
        await get_setting(session, "ncbi_api_key", scope=SettingScope.USER, user_id=user.id)
        == "user-key"
    )


async def test_get_all_masks_secrets(session):
    """get_all : secrets définis → MASK, non-secrets → valeur, non définis → None."""
    await set_setting(session, "ncbi_api_key", "secret-value")
    await set_setting(session, "smtp_host", "mail.example.com")
    result = await get_all(session)
    assert result["ncbi_api_key"] == MASK
    assert result["smtp_host"] == "mail.example.com"
    assert result["llm_api_key"] is None  # non défini


async def test_unknown_key_rejected(session):
    """Clé inconnue → refusée (pas de stockage arbitraire)."""
    with pytest.raises(ValueError):
        await set_setting(session, "totally_unknown_key", "x")


async def test_openalex_api_key_secret(session):
    """Phase 4a : ``openalex_api_key`` est une clé connue, sécrète (chiffrée, masquée)."""
    await set_setting(session, "openalex_api_key", "ok-123")
    assert await get_setting(session, "openalex_api_key") == "ok-123"
    row = (
        await session.execute(
            select(SystemSettings).where(SystemSettings.key_name == "openalex_api_key")
        )
    ).scalar_one()
    assert row.is_secret is True
    assert b"ok-123" not in row.value_encrypted
    assert (await get_all(session))["openalex_api_key"] == MASK
