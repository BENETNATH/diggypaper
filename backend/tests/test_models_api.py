"""Tests des routes modèle (app/api/v1/models.py) — 100 % offline."""

import json
from contextlib import contextmanager

import httpx
import pytest
from sqlalchemy import select

from app.db.models import (
    BenchmarkRun,
    BenchmarkStatus,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
)


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


async def test_model_payload_internal_flag(client, session, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    for r in (
        await client.post("/api/v1/models", json={"name": "Loc", "model_identifier": "kef/kev-4b"}),
        await client.post(
            "/api/v1/models",
            json={"name": "Priv", "backend_type": "OPENAI_API", "model_identifier": "http://10.0.0.5/v1#m"},
        ),
        await client.post(
            "/api/v1/models",
            json={"name": "Pub", "backend_type": "OPENAI_API", "model_identifier": "https://api.openai.com/v1#gpt-4"},
        ),
    ):
        assert r.status_code == 201, r.text
    resp = await client.get("/api/v1/models")
    internal = {m["name"]: m["internal"] for m in resp.json()["items"]}
    assert internal == {"Loc": True, "Priv": True, "Pub": False}


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
        await client.post("/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"})
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
        await client.post("/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"})
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
        await client.post("/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"})
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
        await client.post("/api/v1/models", json={"name": "Q", "model_identifier": "qwen3:4b"})
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
        (await session.execute(select(ModelRegistry).where(ModelRegistry.name == "Q")))
        .scalar_one()
        .id
    )
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
    assert (body["model_identifier"], body["endpoint"]) == ("gpt-4o", "https://api.x")
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


# ---------- POST /models/{id}/test : confirmer qu'un modèle installé est opérationnel ----------


async def test_test_installed_ollama_ok(client, admin_user, do_login, monkeypatch):
    import app.api.v1.models as m

    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models",
        json={"name": "Q8", "backend_type": "OLLAMA", "model_identifier": "qwen3:8b"},
    )
    assert resp.status_code == 201
    mid = resp.json()["id"]

    calls = []

    async def fake_infer(self, model, system_prompt, user_prompt):
        calls.append((model, user_prompt))
        return {"ok": True}

    monkeypatch.setattr(m.OpenAICompatibleService, "infer", fake_infer)
    t = await client.post(f"/api/v1/models/{mid}/test")
    assert t.status_code == 200
    assert t.json() == {"ok": True}
    assert calls == [("qwen3:8b", "ping")]


async def test_test_installed_openai_uses_model_url_and_key(client, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models/external",
        json={
            "name": "GPT",
            "base_url": "https://api.x/v1",
            "model": "gpt-4o",
            "api_key": "sk-1",
        },
    )
    assert resp.status_code == 201
    mid = resp.json()["id"]

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    with _http(handler):
        t = await client.post(f"/api/v1/models/{mid}/test")
    assert t.status_code == 200
    assert t.json() == {"ok": True}
    assert seen == {"url": "https://api.x/v1/chat/completions", "auth": "Bearer sk-1"}


async def test_test_installed_error_sanitized(client, admin_user, do_login, monkeypatch):
    import app.api.v1.models as m
    from app.inference import InferenceError

    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models",
        json={"name": "Q4", "backend_type": "OLLAMA", "model_identifier": "qwen3:4b"},
    )
    mid = resp.json()["id"]

    async def boom(self, model, system_prompt, user_prompt):
        raise InferenceError("timeout")

    monkeypatch.setattr(m.OpenAICompatibleService, "infer", boom)
    t = await client.post(f"/api/v1/models/{mid}/test")
    assert t.status_code == 200
    assert t.json() == {"ok": False, "error": "timeout"}


async def test_test_installed_unknown_404(client, admin_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    t = await client.post("/api/v1/models/00000000-0000-4000-8000-000000000000/test")
    assert t.status_code == 404


async def test_test_installed_forbidden_other_user(client, admin_user, researcher_user, do_login):
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.post(
        "/api/v1/models",
        json={"name": "Q8", "backend_type": "OLLAMA", "model_identifier": "qwen3:8b"},
    )
    mid = resp.json()["id"]
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    t = await client.post(f"/api/v1/models/{mid}/test")
    assert t.status_code == 403


# =====================================================================================
# Correctifs revue OLLAMA (spec 2026-10-29-correctifs-revue-ollama)
# =====================================================================================


def _recorder():
    """Handler Ollama qui journalise les appels (200)."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "choices": [{"message": {"content": '{"ok": true}'}}],
            },
        )

    return seen, handler


async def _row_id(session, name: str):
    return (
        await session.execute(select(ModelRegistry.id).where(ModelRegistry.name == name))
    ).scalar_one()


async def _names(client) -> list[str]:
    return [m["name"] for m in (await client.get("/api/v1/models")).json()["items"]]


# --- §1 DELETE /models/{id} ---


async def test_delete_ollama_model_shared_identifier_keeps_weights(
    client, session, admin_user, do_login
):
    """Une autre ligne du registre utilise les mêmes poids : la ligne part, Ollama n'est PAS
    appelé (sinon l'autre modèle serait cassé)."""
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(ModelRegistry(name="Q-bis", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204
    assert seen == []
    assert await _names(client) == ["Q-bis"]


@pytest.mark.parametrize(
    "main, pre, status",
    [
        ("Q", None, InferenceJobStatus.PENDING),
        ("Autre", "Q", InferenceJobStatus.RUNNING),
    ],
    ids=["principal-pending", "prefiltre-running"],
)
async def test_delete_model_in_use_by_active_inference_job_409(
    client, session, admin_user, do_login, main, pre, status
):
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(InferenceJob(model_used=main, prefilter_model=pre, status=status))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "model_in_use"
    assert seen == []  # refus AVANT tout appel Ollama
    assert "Q" in await _names(client)


async def test_delete_model_in_use_by_active_benchmark_409(client, session, admin_user, do_login):
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(BenchmarkRun(models_tested=["Autre", "Q"], status=BenchmarkStatus.RUNNING))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "model_in_use"
    assert seen == []


async def test_delete_model_after_finished_jobs_ok(client, session, admin_user, do_login):
    """Garde : seuls les jobs PENDING/RUNNING bloquent (un job terminé n'empêche rien)."""
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(InferenceJob(model_used="Q", status=InferenceJobStatus.COMPLETED))
    session.add(BenchmarkRun(models_tested=["Q"], status=BenchmarkStatus.FAILED))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204
    assert len(seen) == 1


async def test_delete_ollama_model_owned_by_non_admin_403_before_ollama(
    client, session, researcher_user, do_login
):
    """Défense en profondeur : supprimer des poids Ollama exige ADMIN, même pour le
    « propriétaire » d'une ligne OLLAMA (invariant owner_id NULL non garanti en base)."""
    session.add(
        ModelRegistry(
            name="Q",
            backend_type="OLLAMA",
            model_identifier="qwen3:4b",
            owner_id=researcher_user.id,
        )
    )
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 403
    assert seen == []


async def test_delete_ollama_model_shared_identifier_latest_alias_keeps_weights(
    client, session, admin_user, do_login
):
    """« qwen3 » et « qwen3:latest » désignent les mêmes poids Ollama : Ollama non appelé."""
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3"))
    session.add(ModelRegistry(name="Q-lat", backend_type="OLLAMA", model_identifier="qwen3:latest"))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204
    assert seen == []
    assert await _names(client) == ["Q-lat"]


async def test_delete_ollama_model_same_identifier_other_backend_still_deletes_weights(
    client, session, admin_user, do_login
):
    """Une ligne d'un autre backend au même identifiant ne partage pas les poids Ollama."""
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(ModelRegistry(name="Q-gguf", backend_type="LLAMA_CPP", model_identifier="qwen3:4b"))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204
    assert len(seen) == 1


@pytest.mark.parametrize("field", ["model", "prefilter_model"])
async def test_delete_model_in_use_by_waiting_scheduled_search_409(
    client, session, admin_user, do_login, field
):
    """Analyse programmée en attente : elle démarrera avec ce modèle, la suppression est refusée."""
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    schedule = {"model": "Autre", "prefilter_model": None, "selection": {}}
    schedule[field] = "Q"
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(
        Search(project_id=project.id, name="S", schedule=schedule, schedule_state="waiting")
    )
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "model_in_use"
    assert seen == []


async def test_delete_model_started_scheduled_search_does_not_block(
    client, session, admin_user, do_login
):
    """Garde : seule une programmation « waiting » bloque (started/failed : plus d'attente)."""
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    session.add(
        Search(project_id=project.id, name="S", schedule={"model": "Q"}, schedule_state="started")
    )
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204


async def test_delete_model_in_use_non_owner_gets_403_not_409(
    client, session, admin_user, researcher_user, do_login
):
    """La permission précède le contrôle d'usage : pas de fuite d'activité d'autrui."""
    session.add(
        ModelRegistry(
            name="Ext",
            backend_type="OPENAI_API",
            model_identifier="gpt",
            owner_id=admin_user.id,
        )
    )
    session.add(InferenceJob(model_used="Ext", status=InferenceJobStatus.RUNNING))
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Ext')}")
    assert resp.status_code == 403


async def test_delete_openai_api_model_in_use_409(client, session, admin_user, do_login):
    session.add(ModelRegistry(name="Ext", backend_type="OPENAI_API", model_identifier="gpt"))
    session.add(InferenceJob(model_used="Ext", status=InferenceJobStatus.PENDING))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Ext')}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "model_in_use"


async def test_delete_model_missing_404(client, session, admin_user, do_login):
    import uuid

    await do_login("admin@lab.fr", "admin-pass-123")
    resp = await client.delete(f"/api/v1/models/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "mode, base_url",
    [("off", None), ("host", "http://ollama:11434/v1")],
    ids=["off", "host-base-compose"],
)
async def test_off_or_host_default_delete_ollama_model_removes_row_only(
    client, session, admin_user, do_login, monkeypatch, mode, base_url
):
    """Ollama non géré par l'instance : la ligne registre est supprimée (204), Ollama n'est
    PAS appelé (les poids ne sont pas les nôtres) — plus de ligne indélébile."""
    from app.api.v1 import models as m
    from app.settings.service import set_setting

    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    await session.commit()
    if base_url:
        await set_setting(session, "llm_api_base_url", base_url)
    monkeypatch.setattr(m, "MODE", mode)
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.delete(f"/api/v1/models/{await _row_id(session, 'Q')}")
    assert resp.status_code == 204
    assert seen == []
    assert "Q" not in await _names(client)


# --- §5 POST /models/{id}/test ---


async def test_off_test_installed_ollama_409(client, session, admin_user, do_login, monkeypatch):
    """Mode off : le bouton Test d'un modèle OLLAMA est refusé (409) sans contacter Ollama."""
    from app.api.v1 import models as m

    session.add(ModelRegistry(name="Q", backend_type="OLLAMA", model_identifier="qwen3:4b"))
    await session.commit()
    monkeypatch.setattr(m, "MODE", "off")
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.post(f"/api/v1/models/{await _row_id(session, 'Q')}/test")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ollama_disabled_by_deployment"
    assert seen == []


# --- §2 POST /models/{id}/test : endpoint décidé par le backend du modèle testé ---


async def test_test_installed_llama_cpp_uses_v1(client, session, admin_user, do_login):
    from app.settings.service import set_setting

    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    session.add(ModelRegistry(name="L", backend_type="LLAMA_CPP", model_identifier="llama-big"))
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.post(f"/api/v1/models/{await _row_id(session, 'L')}/test")
    assert resp.json() == {"ok": True}
    assert [str(r.url) for r in seen] == ["http://llm.test/v1/chat/completions"]


# --- POST /models/{id}/test : modèle verrouillé par une analyse → pas de ping concurrent ---


async def _ollama_row(session, name="Q", identifier="qwen3:4b"):
    session.add(ModelRegistry(name=name, backend_type="OLLAMA", model_identifier=identifier))
    await session.commit()


async def test_test_installed_model_locked_returns_busy_without_ping(
    client, session, admin_user, do_login, monkeypatch, fake_redis
):
    from app.api.v1 import models as m
    from app.inference import model_lock_key

    await _ollama_row(session)
    fake_redis.store[model_lock_key("OLLAMA", "qwen3:4b")] = "token-job"
    monkeypatch.setattr(m, "_redis", lambda: fake_redis)
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.post(f"/api/v1/models/{await _row_id(session, 'Q')}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "model_busy"}
    assert seen == []  # Ollama non contacté


async def test_test_installed_model_free_pings(
    client, session, admin_user, do_login, monkeypatch, fake_redis
):
    from app.api.v1 import models as m

    await _ollama_row(session)
    monkeypatch.setattr(m, "_redis", lambda: fake_redis)
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.post(f"/api/v1/models/{await _row_id(session, 'Q')}/test")
    assert resp.json() == {"ok": True}
    assert len(seen) == 1


async def test_test_installed_redis_down_fails_open(
    client, session, admin_user, do_login, monkeypatch
):
    """Redis injoignable : le bouton reste utilisable (ping best-effort), pas de 500."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.api.v1 import models as m

    class DownRedis:
        async def get(self, key):
            raise RedisConnectionError("Error 111 connecting to redis:6379")

        async def aclose(self):
            pass

    await _ollama_row(session)
    monkeypatch.setattr(m, "_redis", lambda: DownRedis())
    await do_login("admin@lab.fr", "admin-pass-123")
    seen, handler = _recorder()
    with _http(handler):
        resp = await client.post(f"/api/v1/models/{await _row_id(session, 'Q')}/test")
    assert resp.json() == {"ok": True}
    assert len(seen) == 1
