"""Tests i18n : résolution Accept-Language, catalogue, emails localisés, erreurs localisées."""

from app.i18n import resolve_lang, t


def test_resolve_lang_supported():
    assert resolve_lang("fr") == "fr"
    assert resolve_lang("en") == "en"
    assert resolve_lang("de") == "de"
    assert resolve_lang("es") == "es"


def test_resolve_lang_with_qvalues_and_regions():
    # Le test d'origine était trompeur : il passait avec un parser qui ignorait les
    # q-values (la 1re langue supportée dans l'ordre d'apparition gagnait). On vérifie
    # maintenant le tri par q décroissant (RFC 4647).
    assert resolve_lang("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert resolve_lang("es-ES") == "es"
    assert resolve_lang("pt-BR,fr;q=0.5") == "fr"
    # Cas où l'ordre d'apparition ne suffit pas : fr a un q plus haut que en.
    assert resolve_lang("en;q=0.5,fr;q=0.9") == "fr"


def test_resolve_lang_qvalue_edge_cases():
    # q absent → 1.0 ; q=0 → exclu ; code inconnu → ignoré ; égalité → ordre d'apparition.
    assert resolve_lang("en;q=0.5,fr;q=0.9") == "fr"
    assert resolve_lang("fr;q=0") == "en"  # q=0 → exclu, fallback en
    assert resolve_lang("xx,en;q=0.3") == "en"  # code inconnu ignoré
    assert resolve_lang("en;q=0.9,fr;q=0.9") == "en"  # égalité → 1re dans l'ordre


def test_resolve_lang_fallback_en():
    assert resolve_lang("pt-BR") == "en"
    assert resolve_lang("xx,yy") == "en"
    assert resolve_lang(None) == "en"
    assert resolve_lang("") == "en"


def test_t_fallback_and_params():
    assert t("fr", "consent_required") != t("en", "consent_required")
    assert t("pt", "consent_required") == t("en", "consent_required")
    assert t("en", "invite_body", link="http://x") == t("en", "invite_body").format(link="http://x")


async def test_invite_email_in_french(client, admin_user, mock_mailer, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/auth/invite", json={"email": "n@lab.fr", "language": "fr"})
    assert resp.status_code == 202
    msg = mock_mailer.sent[0]
    link = next(line for line in msg["body"].splitlines() if "/register?token=" in line).strip()
    assert msg["subject"] == t("fr", "invite_subject")
    assert msg["body"] == t("fr", "invite_body", link=link)


async def test_invite_email_in_german(client, admin_user, mock_mailer, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/auth/invite", json={"email": "n@lab.fr", "language": "de"})
    assert resp.status_code == 202
    assert mock_mailer.sent[0]["subject"] == t("de", "invite_subject")


async def test_invite_invalid_language_422(client, admin_user, mock_mailer, do_login):
    """m4 : language invalide (hors fr/en/de/es) → 422, aucun email envoyé."""
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/auth/invite", json={"email": "n@lab.fr", "language": "pt"})
    assert resp.status_code == 422
    assert mock_mailer.sent == []


async def test_consent_error_localized(client, researcher_user):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "researcher@lab.fr", "password": "researcher-pass-123"},
        headers={"Accept-Language": "fr"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == t("fr", "consent_required")


def test_default_language_from_env(monkeypatch):
    from app.core.config import get_settings
    from app.i18n import default_lang, resolve_lang

    monkeypatch.setenv("DEFAULT_LANGUAGE", "fr")
    get_settings.cache_clear()
    try:
        assert default_lang() == "fr" and resolve_lang(None) == "fr"
        assert resolve_lang("de") == "de"  # l'en-tête explicite prime
        monkeypatch.setenv("DEFAULT_LANGUAGE", "xx")
        get_settings.cache_clear()
        assert default_lang() == "en"  # valeur invalide : repli sûr
    finally:
        monkeypatch.delenv("DEFAULT_LANGUAGE", raising=False)
        get_settings.cache_clear()
