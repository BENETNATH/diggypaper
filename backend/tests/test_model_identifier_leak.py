"""Fuite d'identifiant de modèle OPENAI_API (spec specs/2026-10-29-fuite-identifiant-modele.md).

Un ``model_identifier`` OPENAI_API vaut ``<base_url>#<modèle>`` : la base URL ne doit jamais
porter de secret (userinfo, query, fragment) et ne sort JAMAIS telle quelle du serveur — ni pour
le propriétaire, ni pour les destinataires d'un partage. 100 % offline.
"""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.db.models import ModelRegistry, UserRole
from tests.conftest import _make_user

API = "/api/v1"
SECRETS_DETAIL = "base_url must not contain credentials, query or fragment"
# Ligne héritée : userinfo + port + segment de chemin porteur d'un jeton + query secrète.
LEGACY = "https://bob:pw-SECRET@api.x.test:8443/v1/tok-PATHSECRET?key=SECRET-Q#gpt-4o"
SECRET_BITS = ("SECRET", "pw-", "bob:", "key=", "tok-PATH", "/v1")

BAD_URLS = {
    "userinfo": "https://alice:pw-SECRET@api.x.test/v1",
    "user-only": "https://tok-SECRET@api.x.test/v1",
    "query": "https://api.x.test/v1?key=SECRET-Q",
    "empty-query": "https://api.x.test/v1?",
    "fragment": "https://api.x.test/v1#SECRET-F",
}


@pytest.fixture
def public_urls(monkeypatch):
    """Pas de DNS en test : toute URL est « publique » (contrôle SSRF neutralisé)."""
    monkeypatch.setattr("app.api.v1.models.check_public_url", lambda url: None)


@pytest.fixture
async def alice(session):
    return await _make_user(session, "alice@lab.fr", UserRole.RESEARCHER, "alice-pass-123", True)


@pytest.fixture
async def bob(session):
    return await _make_user(session, "bob@lab.fr", UserRole.RESEARCHER, "bob-pass-12345", True)


async def _login(client, email, password):
    client.cookies.clear()
    r = await client.post(
        f"{API}/auth/login", json={"email": email, "password": password, "consent": True}
    )
    assert r.status_code == 200, r.text


@contextmanager
def _http(calls: list):
    """Transport httpx global mocké : enregistre toute requête sortante."""

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        yield
    finally:
        httpx.AsyncClient = real


def _assert_no_secret(text: str) -> None:
    for bit in SECRET_BITS:
        assert bit not in text, f"{bit!r} fuit dans {text!r}"


def _item(resp, name):
    return next(m for m in resp.json()["items"] if m["name"] == name)


# --- 1. Rejet à l'enregistrement / la mise à jour ------------------------------------------


@pytest.mark.parametrize("kind", list(BAD_URLS))
async def test_register_external_rejects_url_secrets_admin(
    client, session, admin_user, do_login, kind
):
    """Admin (pas de contrôle SSRF) : 422 quand même, détail fixe, secret jamais renvoyé."""
    await do_login("admin@lab.fr", "admin-pass-123")
    r = await client.post(
        f"{API}/models/external",
        json={"name": "GPT", "base_url": BAD_URLS[kind], "model": "gpt-4o", "api_key": "sk-1"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == SECRETS_DETAIL
    assert "SECRET" not in r.text and "api.x.test" not in r.text
    assert (await session.execute(select(ModelRegistry))).first() is None


async def test_register_external_rejects_url_secrets_non_admin(client, session, alice, public_urls):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    for url in BAD_URLS.values():
        r = await client.post(
            f"{API}/models/external", json={"name": "Mine", "base_url": url, "model": "m"}
        )
        assert r.status_code == 422, url
        assert r.json()["detail"] == SECRETS_DETAIL
        assert "SECRET" not in r.text
    assert (await session.execute(select(ModelRegistry))).first() is None


async def test_register_external_rejects_hash_in_model_name(client, session, admin_user, do_login):
    """« # » est le séparateur URL/modèle : interdit dans le nom du modèle (un seul « # »)."""
    await do_login("admin@lab.fr", "admin-pass-123")
    r = await client.post(
        f"{API}/models/external",
        json={"name": "GPT", "base_url": "https://api.x.test/v1", "model": "gpt#SECRET"},
    )
    assert r.status_code == 422
    assert "SECRET" not in r.text


async def test_external_test_rejects_url_secrets_before_any_call(
    client, session, admin_user, do_login
):
    await do_login("admin@lab.fr", "admin-pass-123")
    calls: list = []
    with _http(calls):
        for url in BAD_URLS.values():
            r = await client.post(
                f"{API}/models/external/test",
                json={"name": "GPT", "base_url": url, "model": "gpt-4o"},
            )
            assert r.status_code == 422, url
            assert r.json()["detail"] == SECRETS_DETAIL and "SECRET" not in r.text
    assert calls == []  # rien n'est parti vers l'extérieur


async def test_create_model_openai_api_rejects_url_secrets(client, session, admin_user, do_login):
    """POST /models (admin) est l'autre porte d'entrée d'une ligne OPENAI_API."""
    await do_login("admin@lab.fr", "admin-pass-123")
    for url in BAD_URLS.values():
        r = await client.post(
            f"{API}/models",
            json={"name": "GPT", "backend_type": "OPENAI_API", "model_identifier": f"{url}#gpt"},
        )
        assert r.status_code == 422, url
        assert "SECRET" not in r.text
    assert (await session.execute(select(ModelRegistry))).first() is None


async def test_patch_openai_identifier_rejects_secrets_and_bad_format(
    client, session, admin_user, do_login
):
    m = ModelRegistry(
        name="GPT", backend_type="OPENAI_API", model_identifier="https://api.x.test/v1#gpt-4o"
    )
    session.add(m)
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    bad = [f"{u}#gpt-4o" for u in BAD_URLS.values() if "#" not in u]
    bad += [
        "gpt-4o",  # identifiant masqué renvoyé tel quel : refusé, la ligne n'est pas corrompue
        "https://api.x.test/v1#",  # modèle vide
        "#gpt-4o",  # URL vide
        "https://api.x.test/v1#SECRET-F#gpt-4o",  # deux « # »
    ]
    for ident in bad:
        r = await client.patch(f"{API}/models/{m.id}", json={"model_identifier": ident})
        assert r.status_code == 422, ident
        assert "SECRET" not in r.text
    await session.refresh(m)
    assert m.model_identifier == "https://api.x.test/v1#gpt-4o"
    ok = await client.patch(
        f"{API}/models/{m.id}", json={"model_identifier": "https://api.y.test/v2#gpt-5"}
    )
    assert ok.status_code == 200
    assert (ok.json()["model_identifier"], ok.json()["endpoint"]) == ("gpt-5", "https://api.y.test")


# --- 2. Masquage en sortie (lignes héritées déjà en base) ----------------------------------


async def test_register_payload_is_masked(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    r = await client.post(
        f"{API}/models/external",
        json={
            "name": "GPT",
            "base_url": "https://api.x.test:8443/v1/tok-PATHSECRET",
            "model": "gpt-4o",
            "api_key": "sk-SECRET",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert (body["model_identifier"], body["endpoint"]) == ("gpt-4o", "https://api.x.test:8443")
    _assert_no_secret(r.text)
    row = (await session.execute(select(ModelRegistry))).scalar_one()
    # stockage intact : l'appel au fournisseur garde le chemin complet
    assert row.model_identifier == "https://api.x.test:8443/v1/tok-PATHSECRET#gpt-4o"


async def test_legacy_row_with_secrets_masked_in_every_payload(
    client, session, admin_user, alice, bob, public_urls
):
    m = ModelRegistry(
        name="Legacy", backend_type="OPENAI_API", model_identifier=LEGACY, owner_id=alice.id
    )
    session.add(m)
    await session.commit()

    await _login(client, "alice@lab.fr", "alice-pass-123")  # propriétaire
    r = await client.get(f"{API}/models")
    _assert_no_secret(r.text)
    owner_view = _item(r, "Legacy")
    assert (owner_view["model_identifier"], owner_view["endpoint"]) == (
        "gpt-4o",
        "https://api.x.test:8443",
    )
    s = await client.post(f"{API}/models/{m.id}/shares", json={"email": "bob@lab.fr"})
    assert s.status_code == 201
    _assert_no_secret(s.text)
    _assert_no_secret((await client.get(f"{API}/models/{m.id}/shares")).text)
    p = await client.patch(f"{API}/models/{m.id}", json={"is_active": True})
    assert p.status_code == 200
    _assert_no_secret(p.text)

    await _login(client, "bob@lab.fr", "bob-pass-12345")  # destinataire du partage
    r = await client.get(f"{API}/models")
    _assert_no_secret(r.text)
    shared_view = _item(r, "Legacy")
    # jamais plus que le propriétaire
    assert {k: v for k, v in shared_view.items() if k != "mine"} == {
        k: v for k, v in owner_view.items() if k != "mine"
    }

    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.get(f"{API}/models")
    _assert_no_secret(r.text)
    assert _item(r, "Legacy")["model_identifier"] == "gpt-4o"


@pytest.mark.parametrize(
    "ident, expected",
    [
        ("https://api.x.test/v1#FRAG-SECRET#gpt-4o", ("gpt-4o", "https://api.x.test")),
        ("https://u:pw-SECRET@api.x.test/v1", ("", "https://api.x.test")),  # sans « # »
        ("llm-SECRET:8000/v1#gpt-4o", ("gpt-4o", None)),  # sans schéma : aucun endpoint
    ],
    ids=["fragment-legacy", "no-hash", "no-scheme"],
)
async def test_odd_legacy_identifiers_masked(
    client, session, admin_user, do_login, ident, expected
):
    session.add(ModelRegistry(name="Odd", backend_type="OPENAI_API", model_identifier=ident))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    r = await client.get(f"{API}/models")
    assert "SECRET" not in r.text
    item = _item(r, "Odd")
    assert (item["model_identifier"], item["endpoint"]) == expected


@pytest.mark.parametrize(
    "ident, expected",
    [
        ("https://api.x.test/v1#FRAG-SECRET#gpt-4o", ("gpt-4o", "https://api.x.test")),
        ("https://u:pw-SECRET@api.x.test/v1", (None, "https://api.x.test")),
    ],
    ids=["fragment-legacy", "no-hash"],
)
def test_public_model_ref_never_returns_url_parts(ident, expected):
    from app.inference import public_model_ref

    assert public_model_ref("OPENAI_API", ident) == expected


async def test_local_backends_payload_unchanged(client, session, admin_user, do_login):
    """Garde : OLLAMA & co. gardent leur identifiant ; ``endpoint`` vaut None."""
    session.add(ModelRegistry(name="Kev", backend_type="OLLAMA", model_identifier="kev:q4"))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    item = _item(await client.get(f"{API}/models"), "Kev")
    assert (item["model_identifier"], item["endpoint"]) == ("kev:q4", None)


# --- 3. Garde : chemin heureux et SSRF inchangés ---------------------------------------------


async def test_clean_url_with_path_still_registers_and_ssrf_unchanged(
    client, session, alice, admin_user
):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    r = await client.post(
        f"{API}/models/external",
        json={"name": "Int", "base_url": "http://127.0.0.1:11434/v1", "model": "m"},
    )
    assert r.status_code == 422 and r.json()["detail"] == "base_url must be a public address"
    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.post(
        f"{API}/models/external",
        json={"name": "GW", "base_url": "https://gw.x.test/openai/v1", "model": "gpt-4o"},
    )
    assert r.status_code == 201
    row = (await session.execute(select(ModelRegistry))).scalar_one()
    assert row.model_identifier == "https://gw.x.test/openai/v1#gpt-4o"


# --- 4. Migration de nettoyage 0031 ---------------------------------------------------------


def _migration():
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    found = sorted(versions.glob("0031_*.py"))
    assert len(found) == 1, "migration 0031 attendue"
    spec = importlib.util.spec_from_file_location("migration_0031", found[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0031_scrubs_openai_identifiers(monkeypatch):
    mod = _migration()
    assert mod.revision == "0031" and mod.down_revision == "0030"
    rows = {
        "userinfo-query": (
            "OPENAI_API",
            "https://bob:pw@api.x.test:8443/v1/proxy?key=K#gpt-4o",
            "https://api.x.test:8443/v1/proxy#gpt-4o",
        ),
        "fragment": (
            "OPENAI_API",
            "https://api.x.test/v1#FRAG#gpt-4o",
            "https://api.x.test/v1#gpt-4o",
        ),
        "ipv6": (
            "OPENAI_API",
            "http://u:p@[2001:db8::1]:8080/v1#m",
            "http://[2001:db8::1]:8080/v1#m",
        ),
        "no-hash": ("OPENAI_API", "https://u:p@api.x.test/v1", "https://api.x.test/v1"),
        "clean": ("OPENAI_API", "https://api.x.test/v1#gpt-4o", "https://api.x.test/v1#gpt-4o"),
        "ollama": ("OLLAMA", "hf.co/u:p@x?y#z", "hf.co/u:p@x?y#z"),  # autres backends : intacts
    }
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE model_registry (id VARCHAR PRIMARY KEY, name VARCHAR, "
                "backend_type VARCHAR, model_identifier VARCHAR NOT NULL)"
            )
        )
        for rid, (backend, ident, _) in rows.items():
            conn.execute(
                sa.text("INSERT INTO model_registry VALUES (:i, :i, :b, :m)"),
                {"i": rid, "b": backend, "m": ident},
            )

        class Op:
            def get_bind(self):
                return conn

        monkeypatch.setattr(mod, "op", Op())
        mod.upgrade()
        got = dict(conn.execute(sa.text("SELECT id, model_identifier FROM model_registry")).all())
        assert got == {rid: expected for rid, (_, _, expected) in rows.items()}
        mod.upgrade()  # idempotente
        mod.downgrade()  # no-op : on ne remet jamais un secret en clair
        again = dict(conn.execute(sa.text("SELECT id, model_identifier FROM model_registry")).all())
        assert again == got
