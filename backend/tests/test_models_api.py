"""Tests des routes modèle (app/api/v1/models.py) — 100 % offline."""

import json
from contextlib import contextmanager

import httpx

from app.db.models import ModelRegistry, Project, ProjectMember, ProjectMemberRole


@contextmanager
def _http(handler):
    """Mock du transport httpx global (pattern des tests de suppression)."""
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        yield
    finally:
        httpx.AsyncClient = real


async def _give_project_membership(session, user) -> Project:
    """Donne à `user` une membership (pour le droit de lecture des modèles)."""
    project = Project(title="P", created_by=user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    return project


async def test_create_model_admin(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models",
        json={
            "name": "Kev-4B",
            "backend_type": "OLLAMA",
            "model_identifier": "kef/kev-4b",
            "ram_usage_mb": 4000,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Kev-4B"
    assert body["is_active"] is True


async def test_list_models(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "kef/kev-4b"})
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()["items"]]
    assert "Kev-4B" in names


async def test_create_model_duplicate_409(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "kef/kev-4b"})
    resp = await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "x"})
    assert resp.status_code == 409


async def test_create_model_non_admin_403(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "x"})
    assert resp.status_code == 403


async def test_create_model_bad_backend_422(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models", json={"name": "X", "backend_type": "WRONG", "model_identifier": "x"}
    )
    assert resp.status_code == 422


async def test_list_models_researcher_with_project(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    await _give_project_membership(session, researcher_user)
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 200


async def test_list_models_researcher_sees_only_accessible(
    client, session, admin_user, researcher_user, do_login
):
    """Sans modèle à soi ni partagé : liste vide (plus de 403, plus d'accès global)."""
    session.add(ModelRegistry(name="Platform", model_identifier="x"))
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_update_model_admin(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "x"})
    ).json()["id"]
    resp = await client.patch(
        f"/api/v1/models/{mid}", json={"is_active": False, "ram_usage_mb": 999}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["ram_usage_mb"] == 999


async def test_update_model_404(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.patch(
        "/api/v1/models/00000000-0000-0000-0000-000000000000", json={"is_active": False}
    )
    assert resp.status_code == 404


async def test_delete_model_admin(client, session, admin_user, do_login):
    """Mock Ollama requis : sans lui, DELETE pointerait vers localhost:11434 et ferait 502."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"deleted": True})

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post("/api/v1/models", json={"name": "Kev-4B", "model_identifier": "x"})
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 204
    assert (await client.get("/api/v1/models")).json()["items"] == []


async def test_delete_ollama_model_calls_ollama_delete_then_removes_row(
    client, session, admin_user, do_login
):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        assert req.url.path == "/api/delete"
        assert json.loads(req.content) == {"name": "qwen3:4b"}
        seen.append(req)
        return httpx.Response(200, json={"deleted": True})

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post(
            "/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"}
        )
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 204
    assert (await client.get("/api/v1/models")).json()["items"] == []
    assert len(seen) == 1


async def test_delete_ollama_model_missing_on_ollama_still_removes_row(
    client, session, admin_user, do_login
):
    """Idempotence : modèle déjà absent d'Ollama (404) → la ligne registre part quand même."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'qwen3:4b' not found"})

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post(
            "/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"}
        )
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 204
    assert (await client.get("/api/v1/models")).json()["items"] == []


async def test_delete_ollama_model_ollama_http_error_502_keeps_row(
    client, session, admin_user, do_login
):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post(
            "/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"}
        )
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 502
    assert resp.json()["detail"] == "ollama_delete_failed"
    names = [m["name"] for m in (await client.get("/api/v1/models")).json()["items"]]
    assert "Q" in names


async def test_delete_ollama_model_network_error_502_keeps_row(
    client, session, admin_user, do_login
):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post(
            "/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"}
        )
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 502
    assert resp.json()["detail"] == "ollama_unreachable"
    names = [m["name"] for m in (await client.get("/api/v1/models")).json()["items"]]
    assert "Q" in names


async def test_delete_ollama_model_no_call_for_openai_api_backend(
    client, session, admin_user, do_login
):
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    await do_login("admin@lab.fr", "admin-pass-123")
    mid = (
        await client.post(
            "/api/v1/models/external",
            json={"name": "GPT", "base_url": "https://api.x/v1", "model": "gpt-4o"},
        )
    ).json()["id"]
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{mid}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 204
    assert (await client.get("/api/v1/models")).json()["items"] == []


async def test_delete_ollama_model_forbidden_before_ollama_call(
    client, session, admin_user, researcher_user, do_login
):
    """Le contrôle de permission précède tout appel Ollama (403, handler non appelé)."""

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    from sqlalchemy import select

    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    row = (
        await session.execute(select(ModelRegistry).where(ModelRegistry.name == "Q"))
    ).scalar_one().id
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        resp = await client.delete(f"/api/v1/models/{row}")
    finally:
        httpx.AsyncClient = real
    assert resp.status_code == 403


async def test_ollama_register_admin_only_and_idempotent(
    client, admin_user, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (
        await client.post("/api/v1/models/ollama/register", json={"name": "m:1"})
    ).status_code == 403
    await do_login("admin@lab.fr", "admin-pass-123")
    for _ in range(2):
        assert (
            await client.post("/api/v1/models/ollama/register", json={"name": "m:1"})
        ).status_code == 201
    items = (await client.get("/api/v1/models")).json()["items"]
    assert [m["name"] for m in items] == ["m:1"]


async def test_hf_search_files_and_install_validation(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    async def fake_hf(path, params=None):
        if path == "/api/models":
            return [{"id": "ggml-org/Kev-4B-GGUF", "downloads": 5}, {"id": "bad id"}]
        return [
            {"type": "file", "path": "kev-4b-Q4_K_M.gguf", "size": 10},
            {"type": "file", "path": "mmproj-f16.gguf"},
            {"type": "file", "path": "README.md"},
        ]

    monkeypatch.setattr(m, "_hf_get", fake_hf)
    await do_login("admin@lab.fr", "admin-pass-123")
    r = await client.get("/api/v1/models/search", params={"q": "kev"})
    assert [i["id"] for i in r.json()["items"]] == ["ggml-org/Kev-4B-GGUF"]
    r = await client.get("/api/v1/models/hf/files", params={"repo": "a/b"})
    assert r.json()["items"] == [
        {"file": "kev-4b-Q4_K_M.gguf", "quant": "Q4_K_M", "size": 10, "recommended": True}
    ]
    bad = {"repo": "../x"}
    assert (await client.post("/api/v1/models/hf/install", json=bad)).status_code == 422


async def test_hf_decision_models_flagged_and_not_installable(
    client, admin_user, do_login, monkeypatch
):
    from app.api.v1 import models as m

    async def fake_hf(path, params=None):
        if path == "/api/models":
            return [
                {"id": "ggml-org/Kev-4B-GGUF", "tags": ["gguf", "decision-model"]},
                {"id": "unsloth/Qwen3-4B-GGUF", "tags": ["gguf"]},
            ]
        return {"tags": ["decision-model"] if "Kev" in path else ["gguf"]}

    monkeypatch.setattr(m, "_hf_get", fake_hf)
    await do_login("admin@lab.fr", "admin-pass-123")
    items = (await client.get("/api/v1/models/search", params={"q": "kev"})).json()["items"]
    assert {i["id"]: i["decision"] for i in items} == {
        "ggml-org/Kev-4B-GGUF": True,
        "unsloth/Qwen3-4B-GGUF": False,
    }
    r = await client.post("/api/v1/models/hf/install", json={"repo": "ggml-org/Kev-4B-GGUF"})
    assert r.status_code == 422 and r.json()["detail"] == "decision_model"
    assert m._PULLS == {}  # rien de lancé


async def test_hf_files_sorted_for_cpu(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    async def fake_hf(path, params=None):
        return [
            {"type": "file", "path": "x-BF16.gguf", "size": 80},
            {"type": "file", "path": "x-Q8_0.gguf", "size": 40},
            {"type": "file", "path": "x-Q4_K_M-00001-of-00002.gguf", "size": 10},
            {"type": "file", "path": "x-Q4_K_M-00002-of-00002.gguf", "size": 10},
            {"type": "file", "path": "x-IQ2_XS.gguf", "size": 5},
        ]

    monkeypatch.setattr(m, "_hf_get", fake_hf)
    await do_login("admin@lab.fr", "admin-pass-123")
    items = (await client.get("/api/v1/models/hf/files", params={"repo": "a/b"})).json()["items"]
    assert [i["quant"] for i in items] == ["Q4_K_M", "Q8_0", "IQ2_XS", "BF16"]
    assert items[0]["size"] == 20 and items[0]["recommended"] is True
    assert not any(i["recommended"] for i in items[1:])


async def test_register_external_encrypts_key(client, session, admin_user, do_login):
    from sqlalchemy import select

    from app.core.crypto import decrypt_secret
    from app.db.models import ModelRegistry

    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models/external",
        json={
            "name": "GPT",
            "base_url": "https://api.x/v1",
            "model": "gpt-4o",
            "api_key": "sk-secret",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["backend_type"] == "OPENAI_API"
    assert body["model_identifier"] == "https://api.x/v1#gpt-4o"
    assert "sk-secret" not in resp.text
    row = (
        await session.execute(select(ModelRegistry).where(ModelRegistry.name == "GPT"))
    ).scalar_one()
    assert decrypt_secret(row.api_key_enc) == "sk-secret"


async def test_infer_with_uses_model_url_and_key():
    import httpx

    from app.core.crypto import encrypt_secret
    from app.db.models import ModelRegistry
    from app.inference import OpenAICompatibleService, infer_with

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})

    m = ModelRegistry(
        name="GPT",
        backend_type="OPENAI_API",
        model_identifier="https://api.x/v1#gpt-4o",
        api_key_enc=encrypt_secret("sk-1"),
    )
    default = OpenAICompatibleService("http://unused/v1")
    # le service créé par infer_with n'a pas de client injecté : on patche le transport global
    real = httpx.AsyncClient
    httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        assert await infer_with(m, default, "s", "u") == {"a": 1}
    finally:
        httpx.AsyncClient = real
    assert seen == {"url": "https://api.x/v1/chat/completions", "auth": "Bearer sk-1"}


async def test_capabilities_reports_ollama_hardware_and_recommendation(
    client, session, admin_user, do_login, monkeypatch
):
    from app import hardware
    from app.api.v1 import models as m

    async def fake_reachable(url, path=""):
        return True

    monkeypatch.setattr(m, "_reachable", fake_reachable)
    monkeypatch.setattr(
        hardware,
        "detect",
        lambda: {
            "cores": 8,
            "ram_gb": 54,
            "avx": True,
            "avx2": False,
            "avx512": False,
            "gpu": False,
        },
    )
    await do_login("admin@lab.fr", "admin-pass-123")
    caps = (await client.get("/api/v1/models/capabilities")).json()
    # Champ `mode` gagné (flag de déploiement, défaut docker) ; le reste est inchangé.
    assert caps["ollama"] == {"mode": "docker", "configured": True, "reachable": True}
    assert caps["hardware"]["ram_gb"] == 54
    rec = caps["recommendation"]
    assert rec["profile"] == "large" and rec["slow_cpu"] is True  # sans AVX2 : avertissement
    assert rec["models"][0]["kind"] == "moe" and rec["models"][0]["fits"] is True


# --- Flag de déploiement OLLAMA_MODE (spec 2026-10-28-ollama-deploy-mode) ----


async def test_capabilities_docker_mode_default_invariant(
    client, admin_user, do_login, monkeypatch
):
    """Mode docker (défaut) : comportement invariant, seul le champ `mode` est ajouté."""
    from app.api.v1 import models as m

    async def fake_reachable(url, path=""):
        return True

    monkeypatch.setattr(m, "MODE", "docker")
    monkeypatch.setattr(m, "_reachable", fake_reachable)
    await do_login("admin@lab.fr", "admin-pass-123")
    caps = (await client.get("/api/v1/models/capabilities")).json()
    assert caps["ollama"] == {"mode": "docker", "configured": True, "reachable": True}


async def test_capabilities_host_mode_custom_url_configured(
    client, session, admin_user, do_login, monkeypatch
):
    from app.api.v1 import models as m
    from app.settings.service import set_setting

    async def fake_reachable(url, path=""):
        return True

    monkeypatch.setattr(m, "MODE", "host")
    monkeypatch.setattr(m, "_reachable", fake_reachable)
    await set_setting(session, "llm_api_base_url", "http://10.0.0.5:11434/v1")
    await do_login("admin@lab.fr", "admin-pass-123")
    caps = (await client.get("/api/v1/models/capabilities")).json()
    assert caps["ollama"] == {"mode": "host", "configured": True, "reachable": True}


async def test_capabilities_host_mode_compose_default_not_configured(
    client, session, admin_user, do_login, monkeypatch
):
    """Mode host + base résiduelle du compose : non configuré, AUCUN ping, (warning loggé)."""
    from app.api.v1 import models as m
    from app.settings.service import set_setting

    async def no_ping(url, path=""):
        raise AssertionError("ping interdit : base résiduelle compose en mode host")

    monkeypatch.setattr(m, "MODE", "host")
    monkeypatch.setattr(m, "_reachable", no_ping)
    await set_setting(session, "llm_api_base_url", "http://ollama:11434/v1")
    await do_login("admin@lab.fr", "admin-pass-123")
    caps = (await client.get("/api/v1/models/capabilities")).json()
    assert caps["ollama"] == {"mode": "host", "configured": False, "reachable": False}


async def test_capabilities_off_mode_no_ping(client, admin_user, do_login, monkeypatch):
    """Mode off : {off, false, false} SANS ping (aucun ping en mode off)."""
    from app.api.v1 import models as m

    async def no_ping(url, path=""):
        raise AssertionError("ping interdit en mode off")

    monkeypatch.setattr(m, "MODE", "off")
    monkeypatch.setattr(m, "_reachable", no_ping)
    await do_login("admin@lab.fr", "admin-pass-123")
    caps = (await client.get("/api/v1/models/capabilities")).json()
    assert caps["ollama"] == {"mode": "off", "configured": False, "reachable": False}


async def test_off_list_ollama_409(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    with _http(handler):
        resp = await client.get("/api/v1/models/ollama")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"


async def test_off_pull_ollama_409_and_no_state(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/models/ollama/pull", json={"name": "qwen3:4b"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    assert m._PULLS == {}  # rien lancé


async def test_off_register_ollama_409(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/models/ollama/register", json={"name": "m:1"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    assert (await client.get("/api/v1/models")).json()["items"] == []  # aucune ligne créée


async def test_off_create_ollama_409_but_openai_api_still_201(
    client, admin_user, do_login, monkeypatch
):
    """Les backends API clé restent opérationnels en mode off."""
    from app.api.v1 import models as m

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    with _http(handler):
        resp = await client.post(
            "/api/v1/models",
            json={"name": "Kev-4B", "backend_type": "OLLAMA", "model_identifier": "kef/kev-4b"},
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    resp = await client.post(
        "/api/v1/models/external",
        json={"name": "GPT", "base_url": "https://api.x/v1", "model": "gpt-4o"},
    )
    assert resp.status_code == 201


async def test_off_delete_ollama_model_409_keeps_row(
    client, session, admin_user, do_login, monkeypatch
):
    """Suppression refusée AVANT tout appel Ollama : la ligne est conservée."""
    from sqlalchemy import select

    from app.api.v1 import models as m

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    await session.commit()
    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    row = (
        await session.execute(select(ModelRegistry).where(ModelRegistry.name == "Q"))
    ).scalar_one().id
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{row}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    names = [m2["name"] for m2 in (await client.get("/api/v1/models")).json()["items"]]
    assert "Q" in names


async def test_off_hf_install_409_before_hf_call(client, admin_user, do_login, monkeypatch):
    from app.api.v1 import models as m

    async def fake_hf(path, params=None):
        raise AssertionError("HF ne doit pas être appelé en mode off")

    monkeypatch.setattr(m, "MODE", "off")
    monkeypatch.setattr(m, "_hf_get", fake_hf)
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post("/api/v1/models/hf/install", json={"repo": "a/b"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    assert m._PULLS == {}  # rien lancé


async def test_host_compose_default_endpoints_refused(
    client, session, admin_user, do_login, monkeypatch
):
    """Mode host + base résiduelle compose : actions Ollama refusées (409, detail précis)."""
    from app.api.v1 import models as m
    from app.settings.service import set_setting

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("Ollama ne doit pas être appelé")

    monkeypatch.setattr(m, "MODE", "host")
    await set_setting(session, "llm_api_base_url", "http://ollama:11434/v1")
    await do_login("admin@lab.fr", "admin-pass-123")
    with _http(handler):
        resp = await client.get("/api/v1/models/ollama")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_base_url_is_compose_default"
    with _http(handler):
        resp = await client.post("/api/v1/models/ollama/register", json={"name": "m:1"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_base_url_is_compose_default"


async def test_host_compose_default_url_variants_detected(
    client, session, admin_user, do_login, monkeypatch
):
    """La dérive de base (sans /v1, rstrip) fait détecter les 3 variantes du compose."""
    from app.api.v1 import models as m
    from app.settings.service import set_setting

    async def no_ping(url, path=""):
        raise AssertionError("ping interdit : base résiduelle compose")

    monkeypatch.setattr(m, "MODE", "host")
    monkeypatch.setattr(m, "_reachable", no_ping)
    await do_login("admin@lab.fr", "admin-pass-123")
    for url in ("http://ollama:11434/v1/", "http://ollama:11434", "http://ollama:11434/v1"):
        await set_setting(session, "llm_api_base_url", url)
        caps = (await client.get("/api/v1/models/capabilities")).json()
        assert caps["ollama"]["configured"] is False, url


def test_recommend_by_resources():
    from app import hardware

    base = {"cores": 8, "avx": True, "avx2": True, "avx512": False, "gpu": False}
    pick = lambda **kw: hardware.recommend({**base, **kw})  # noqa: E731
    assert pick(ram_gb=8)["profile"] == "small"
    assert pick(ram_gb=8)["models"][0]["tag"].startswith("qwen2.5:3b")
    assert pick(ram_gb=24)["profile"] == "medium"
    assert pick(ram_gb=64)["profile"] == "large" and not pick(ram_gb=64)["slow_cpu"]
    assert pick(ram_gb=16, gpu=True)["profile"] == "gpu"
    assert (
        pick(ram_gb=8)["models"][0]["fits"] is True and pick(ram_gb=2)["models"][0]["fits"] is False
    )
