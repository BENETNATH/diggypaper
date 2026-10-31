"""Tests for the admin settings routes (app/api/v1/admin.py, Module 7)."""

import uuid

from sqlalchemy import func, select

from app.core.crypto import MASK
from app.db.models import Project, SettingScope, SystemSettings
from app.settings.service import get_setting, set_setting


async def _login_admin(client, do_login) -> None:
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)


async def _settings_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(SystemSettings))).scalar_one()


# --- GET /admin/settings ---


async def test_get_masks_secrets_and_nulls(client, session, admin_user, do_login):
    await set_setting(session, "ncbi_api_key", "secret-value")
    await set_setting(session, "smtp_host", "mail.example.com")
    await _login_admin(client, do_login)
    resp = await client.get("/api/v1/admin/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["GLOBAL"]["ncbi_api_key"] == MASK
    assert data["GLOBAL"]["smtp_host"] == "mail.example.com"
    assert data["GLOBAL"]["llm_api_key"] is None  # non défini → null


async def test_get_project_scope_via_query_param(client, session, admin_user, do_login):
    project = Project(title="P")
    session.add(project)
    await session.commit()
    await set_setting(
        session, "ncbi_api_key", "proj-secret", scope=SettingScope.PROJECT, project_id=project.id
    )
    await _login_admin(client, do_login)
    resp = await client.get(f"/api/v1/admin/settings?project_id={project.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["PROJECT"]["ncbi_api_key"] == MASK
    assert data["GLOBAL"]["ncbi_api_key"] is None  # pas de valeur GLOBAL


# --- PUT /admin/settings ---


async def test_put_mask_unchanged(client, session, admin_user, do_login):
    """value == MASK → the existing value is not modified."""
    await set_setting(session, "ncbi_api_key", "original-secret")
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": MASK}}
    )
    assert resp.status_code == 200
    assert resp.json()["ncbi_api_key"] == MASK
    assert await get_setting(session, "ncbi_api_key") == "original-secret"


async def test_put_empty_string_deletes(client, session, admin_user, do_login):
    """value == "" → delete_setting (revert to default / None)."""
    await set_setting(session, "ncbi_api_key", "to-delete")
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": ""}}
    )
    assert resp.status_code == 200
    assert resp.json()["ncbi_api_key"] is None
    assert await get_setting(session, "ncbi_api_key") is None


async def test_put_new_value_encrypted_in_db(client, session, admin_user, do_login):
    """New secret value: encrypted in the DB, masked in the response."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"ncbi_api_key": "new-secret-key"}},
    )
    assert resp.status_code == 200
    assert resp.json()["ncbi_api_key"] == MASK
    row = (
        await session.execute(
            select(SystemSettings).where(SystemSettings.key_name == "ncbi_api_key")
        )
    ).scalar_one()
    assert b"new-secret-key" not in row.value_encrypted
    assert await get_setting(session, "ncbi_api_key") == "new-secret-key"


async def test_put_project_scope_requires_project_id(client, admin_user, do_login):
    """PROJECT scope without project_id → 422 (inconsistent scope)."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "PROJECT", "values": {"ncbi_api_key": "x"}}
    )
    assert resp.status_code == 422


async def test_put_user_scope_requires_user_id(client, admin_user, do_login):
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "USER", "values": {"ncbi_api_key": "x"}}
    )
    assert resp.status_code == 422


async def test_put_unknown_key_422(client, admin_user, do_login):
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"unknown_key": "x"}}
    )
    assert resp.status_code == 422


# --- F1: PUT scope + surplus IDs → 422, nothing written ---


async def test_put_global_scope_with_project_id_422(client, session, admin_user, do_login):
    """F1: GLOBAL scope + project_id → 422, no system_settings row created."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={
            "scope": "GLOBAL",
            "project_id": str(uuid.uuid4()),
            "values": {"ncbi_api_key": "x"},
        },
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_project_scope_with_user_id_422(client, session, admin_user, do_login):
    """F1: PROJECT scope + user_id → 422, no row created."""
    project = Project(title="P")
    session.add(project)
    await session.commit()
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={
            "scope": "PROJECT",
            "project_id": str(project.id),
            "user_id": str(uuid.uuid4()),
            "values": {"ncbi_api_key": "x"},
        },
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_user_scope_with_project_id_422(client, session, admin_user, do_login):
    """F1: USER scope + project_id → 422, no row created."""
    project = Project(title="P")
    session.add(project)
    await session.commit()
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={
            "scope": "USER",
            "user_id": str(admin_user.id),
            "project_id": str(project.id),
            "values": {"ncbi_api_key": "x"},
        },
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


# --- F3 : PUT validation smtp_port / smtp_from_email → 422 ---


async def test_put_smtp_port_not_a_number_422(client, session, admin_user, do_login):
    """F3: non-numeric smtp_port → 422, nothing written."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_port": "not-a-port"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_smtp_port_out_of_range_422(client, session, admin_user, do_login):
    """F3: smtp_port outside [1, 65535] → 422, nothing written."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_port": "99999"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_smtp_from_email_invalid_422(client, session, admin_user, do_login):
    """F3: invalid smtp_from_email → 422, nothing written."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_from_email": "invalid"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


# --- autorisation ---


async def test_non_admin_forbidden(client, researcher_user, do_login):
    """A RESEARCHER can neither read nor modify settings."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.get("/api/v1/admin/settings")).status_code == 403
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": "x"}}
    )
    assert resp.status_code == 403


async def test_unauthenticated_401(client):
    assert (await client.get("/api/v1/admin/settings")).status_code == 401


# --- security: no plaintext in responses ---


async def test_secret_plaintext_never_in_any_response(client, session, admin_user, do_login):
    await set_setting(session, "ncbi_api_key", "top-secret-ncbi")
    await _login_admin(client, do_login)
    resp = await client.get("/api/v1/admin/settings")
    assert resp.status_code == 200
    assert "top-secret-ncbi" not in resp.text
    resp2 = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"ncbi_api_key": "another-secret"}},
    )
    assert "another-secret" not in resp2.text


# --- POST /admin/settings/smtp/test ---


def _fake_fastmail(captured: dict, send_error: Exception | None = None):
    """FakeFastMail capturing the config + the message (mocked in app.emails).

    The mock point is ``app.emails.FastMail`` (no longer ``admin_mod.FastMail``):
    admin.py no longer builds a ConnectionConfig/FastMail, it goes through
    ``Mailer.from_settings`` which builds the real FastMail (validated config).
    """

    class FakeFastMail:
        def __init__(self, config) -> None:
            captured["config"] = config

        async def send_message(self, message) -> None:
            if send_error is not None:
                raise send_error
            captured["message"] = message

    return FakeFastMail


async def test_smtp_test_ok(client, session, admin_user, do_login, monkeypatch):
    """Mocked SMTP: DB config (env fallback) + send → 200 {sent: true}."""
    import app.emails as emails_mod

    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_port", "2525")
    await set_setting(session, "smtp_user", "smtp-user")
    await set_setting(session, "smtp_password", "smtp-pass")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await set_setting(session, "use_tls", "false")
    await _login_admin(client, do_login)

    captured: dict[str, object] = {}
    monkeypatch.setattr(emails_mod, "FastMail", _fake_fastmail(captured))
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 200
    assert resp.json() == {"sent": True}
    config = captured["config"]
    assert config.MAIL_SERVER == "smtp.test.local"
    assert config.MAIL_PORT == 2525
    assert config.MAIL_USERNAME == "smtp-user"
    assert config.MAIL_PASSWORD.get_secret_value() == "smtp-pass"
    assert config.MAIL_FROM == "from@example.com"
    assert config.MAIL_STARTTLS is False
    assert captured["message"].recipients[0].email == "admin@lab.fr"  # to absent → email admin


async def test_smtp_test_explicit_to(client, session, admin_user, do_login, monkeypatch):
    import app.emails as emails_mod

    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await _login_admin(client, do_login)

    captured: dict[str, object] = {}
    monkeypatch.setattr(emails_mod, "FastMail", _fake_fastmail(captured))
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={"to": "other@lab.fr"})
    assert resp.status_code == 200
    assert captured["message"].recipients[0].email == "other@lab.fr"


async def test_smtp_test_unreachable_502(client, session, admin_user, do_login, monkeypatch):
    """Unreachable SMTP (mocked) → 502 {sent: false, error: connection}."""
    import app.emails as emails_mod

    await set_setting(session, "smtp_host", "smtp.unreachable.local")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await _login_admin(client, do_login)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        emails_mod, "FastMail", _fake_fastmail(captured, send_error=ConnectionError("refused"))
    )
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: connection"


# --- m2: sanitized SMTP error message (category, no raw str(exc)) ---


async def test_smtp_test_auth_failure_502_sanitized(
    client, session, admin_user, do_login, monkeypatch
):
    """m2: SMTP auth failure → 502, error == 'SMTP test failed: authentication',
    and the response contains neither the password nor 'Traceback'."""
    from aiosmtplib import SMTPAuthenticationError

    import app.emails as emails_mod

    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_user", "smtp-user")
    await set_setting(session, "smtp_password", "super-secret-smtp-pass")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await _login_admin(client, do_login)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        emails_mod,
        "FastMail",
        _fake_fastmail(
            captured,
            send_error=SMTPAuthenticationError(535, "5.7.8 Authentication credentials invalid"),
        ),
    )
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: authentication"
    # The SMTP password and the traceback must NEVER appear in the response.
    assert "super-secret-smtp-pass" not in resp.text
    assert "Traceback" not in resp.text


# --- m1: test_smtp goes through Mailer.from_settings (no duplication) ---


def test_admin_does_not_build_smtp_config_directly():
    """m1: admin.py no longer builds ConnectionConfig/FastMail directly;
    the SMTP config is centralized in Mailer.from_settings."""
    import app.api.v1.admin as admin_mod

    assert not hasattr(admin_mod, "ConnectionConfig")
    assert not hasattr(admin_mod, "FastMail")
    assert not hasattr(admin_mod, "MessageSchema")
    assert not hasattr(admin_mod, "MessageType")


# --- F2/F3: smtp/test invalid config → 502 (not 500) ---


async def test_smtp_test_default_config_502(client, session, admin_user, do_login):
    """F2: default config (empty smtp_from, nothing in DB) → 502 'configuration', not 500.

    from_settings raises a clean ValueError BEFORE building the FastMail.
    """
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


async def test_smtp_test_invalid_from_email_502(client, session, admin_user, do_login):
    """F2: invalid smtp_from_email in the DB → ConnectionConfig fails → 502 'configuration'."""
    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_from_email", "invalid")
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


async def test_smtp_test_non_numeric_port_502(client, session, admin_user, do_login):
    """F3: non-numeric port in the DB → from_settings raises ValueError → 502 'configuration'."""
    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_port", "not-a-port")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


# --- m3: PUT /admin/settings is transactional (rollback if a key fails) ---


async def test_put_partial_failure_rolls_back(client, session, admin_user, do_login, monkeypatch):
    """m3: PUT with 2 keys, the 2nd fails → the 1st is NOT applied (rollback)."""
    import app.api.v1.admin as admin_mod

    await _login_admin(client, do_login)

    real_set_setting = admin_mod.set_setting
    calls = 0

    async def flaky_set_setting(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:  # la 2e clé (smtp_port) échoue
            raise RuntimeError("boom")
        return await real_set_setting(*args, **kwargs)

    monkeypatch.setattr(admin_mod, "set_setting", flaky_set_setting)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_host": "first.example.com", "smtp_port": "2525"}},
    )
    assert resp.status_code == 500
    # The 1st key (smtp_host) is NOT applied: transaction rollback.
    assert await get_setting(session, "smtp_host") is None
    assert await get_setting(session, "smtp_port") is None


# --- GET /admin/settings/env: what comes from .env when the DB is empty ---


async def test_env_values_shown_without_leaking_secrets(client, admin_user, do_login, monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(s, "smtp_port", 465)
    monkeypatch.setattr(s, "smtp_password", "super-secret")
    monkeypatch.setattr(s, "smtp_from", "no-reply@example.org")
    await _login_admin(client, do_login)
    r = await client.get("/api/v1/admin/settings/env")
    assert r.status_code == 200
    env = r.json()
    assert env["smtp_host"] == {"set": True, "value": "smtp.example.org"}
    assert env["smtp_port"] == {"set": True, "value": "465"}
    assert env["smtp_password"] == {"set": True, "value": None}  # jamais en clair
    assert "super-secret" not in r.text
    assert env["smtp_from_email"]["value"] == "no-reply@example.org"
    assert env["smtp_user"] == {"set": False, "value": None}


async def test_env_values_admin_only(client, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.get("/api/v1/admin/settings/env")).status_code == 403
