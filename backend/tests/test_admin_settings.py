"""Tests des routes admin settings (app/api/v1/admin.py, Module 7)."""

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
    """value == MASK → la valeur existante n'est pas modifiée."""
    await set_setting(session, "ncbi_api_key", "original-secret")
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": MASK}}
    )
    assert resp.status_code == 200
    assert resp.json()["ncbi_api_key"] == MASK
    assert await get_setting(session, "ncbi_api_key") == "original-secret"


async def test_put_empty_string_deletes(client, session, admin_user, do_login):
    """value == "" → delete_setting (revert au default / None)."""
    await set_setting(session, "ncbi_api_key", "to-delete")
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": ""}}
    )
    assert resp.status_code == 200
    assert resp.json()["ncbi_api_key"] is None
    assert await get_setting(session, "ncbi_api_key") is None


async def test_put_new_value_encrypted_in_db(client, session, admin_user, do_login):
    """Nouvelle valeur de secret → chiffrée en base, masquée dans la réponse."""
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
    """Scope PROJECT sans project_id → 422 (scope incohérent)."""
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


# --- F1 : PUT scope + IDs superflus → 422, rien écrit ---


async def test_put_global_scope_with_project_id_422(client, session, admin_user, do_login):
    """F1 : scope GLOBAL + project_id → 422, aucune ligne system_settings créée."""
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
    """F1 : scope PROJECT + user_id → 422, aucune ligne créée."""
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
    """F1 : scope USER + project_id → 422, aucune ligne créée."""
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
    """F3 : smtp_port non numérique → 422, rien écrit."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_port": "not-a-port"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_smtp_port_out_of_range_422(client, session, admin_user, do_login):
    """F3 : smtp_port hors [1, 65535] → 422, rien écrit."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_port": "99999"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


async def test_put_smtp_from_email_invalid_422(client, session, admin_user, do_login):
    """F3 : smtp_from_email invalide → 422, rien écrit."""
    await _login_admin(client, do_login)
    resp = await client.put(
        "/api/v1/admin/settings",
        json={"scope": "GLOBAL", "values": {"smtp_from_email": "invalid"}},
    )
    assert resp.status_code == 422
    assert await _settings_count(session) == 0


# --- autorisation ---


async def test_non_admin_forbidden(client, researcher_user, do_login):
    """Un RESEARCHER ne peut ni lire ni modifier les settings."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.get("/api/v1/admin/settings")).status_code == 403
    resp = await client.put(
        "/api/v1/admin/settings", json={"scope": "GLOBAL", "values": {"ncbi_api_key": "x"}}
    )
    assert resp.status_code == 403


async def test_unauthenticated_401(client):
    assert (await client.get("/api/v1/admin/settings")).status_code == 401


# --- sécurité : pas de plaintext dans les réponses ---


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
    """FakeFastMail capturant la config + le message (mocké dans app.emails).

    Le point de mock est ``app.emails.FastMail`` (plus ``admin_mod.FastMail``) :
    admin.py ne construit plus de ConnectionConfig/FastMail, il passe par
    ``Mailer.from_settings`` qui construit le FastMail réel (config validée).
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
    """SMTP mocké : config DB (fallback env) + envoi → 200 {sent: true}."""
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
    """SMTP injoignable (mocké) → 502 {sent: false, error: connection}."""
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


# --- m2 : message d'erreur SMTP assaini (catégorie, pas de str(exc) brut) ---


async def test_smtp_test_auth_failure_502_sanitized(
    client, session, admin_user, do_login, monkeypatch
):
    """m2 : échec d'auth SMTP → 502, error == 'SMTP test failed: authentication',
    et la réponse ne contient ni le password ni 'Traceback'."""
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
    # Le password SMTP et le traceback ne doivent JAMAIS apparaître dans la réponse.
    assert "super-secret-smtp-pass" not in resp.text
    assert "Traceback" not in resp.text


# --- m1 : test_smtp passe bien par Mailer.from_settings (pas de duplication) ---


def test_admin_does_not_build_smtp_config_directly():
    """m1 : admin.py ne construit plus ConnectionConfig/FastMail directement ;
    la config SMTP est centralisée dans Mailer.from_settings."""
    import app.api.v1.admin as admin_mod

    assert not hasattr(admin_mod, "ConnectionConfig")
    assert not hasattr(admin_mod, "FastMail")
    assert not hasattr(admin_mod, "MessageSchema")
    assert not hasattr(admin_mod, "MessageType")


# --- F2/F3 : smtp/test config invalide → 502 (pas 500) ---


async def test_smtp_test_default_config_502(client, session, admin_user, do_login):
    """F2 : config par défaut (smtp_from vide, rien en DB) → 502 'configuration', pas 500.

    from_settings lève une ValueError propre AVANT de construire le FastMail.
    """
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


async def test_smtp_test_invalid_from_email_502(client, session, admin_user, do_login):
    """F2 : smtp_from_email invalide en DB → ConnectionConfig échoue → 502 'configuration'."""
    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_from_email", "invalid")
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


async def test_smtp_test_non_numeric_port_502(client, session, admin_user, do_login):
    """F3 : port non numérique en base → from_settings lève ValueError → 502 'configuration'."""
    await set_setting(session, "smtp_host", "smtp.test.local")
    await set_setting(session, "smtp_port", "not-a-port")
    await set_setting(session, "smtp_from_email", "from@example.com")
    await _login_admin(client, do_login)
    resp = await client.post("/api/v1/admin/settings/smtp/test", json={})
    assert resp.status_code == 502
    data = resp.json()
    assert data["sent"] is False
    assert data["error"] == "SMTP test failed: configuration"


# --- m3 : PUT /admin/settings transactionnel (rollback si une clé échoue) ---


async def test_put_partial_failure_rolls_back(client, session, admin_user, do_login, monkeypatch):
    """m3 : PUT avec 2 clés dont la 2e échoue → la 1re n'est PAS appliquée (rollback)."""
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
    # La 1re clé (smtp_host) n'est PAS appliquée : rollback de la transaction.
    assert await get_setting(session, "smtp_host") is None
    assert await get_setting(session, "smtp_port") is None
