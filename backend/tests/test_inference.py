"""Tests de l'InferenceService (app/inference) — 100 % offline (LLM mocké)."""

import json

import httpx
import pytest

from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    make_inference_service,
    warmup,
)


def _llm_handler(content: str, status: int = 200):
    """MockTransport : renvoie une réponse /chat/completions avec `content`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    return handler


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


VALID_JSON = json.dumps(
    {
        "use_ai_behavior": True,
        "animal_sex": "BOTH_SEXES",
        "confidence_score": 0.9,
        "evidence_quotes": ["mice were randomized"],
        "reasoning_summary": "ok",
    }
)


async def test_infer_returns_parsed_json():
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(VALID_JSON)))
    result = await svc.infer("Kev-4B", "sys", "user")
    assert result["use_ai_behavior"] is True
    assert result["animal_sex"] == "BOTH_SEXES"
    assert result["confidence_score"] == 0.9


async def test_infer_invalid_json_raises():
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler("not json")))
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "invalid_json" in str(exc.value)


async def test_infer_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(handler))
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "timeout" in str(exc.value)


async def test_infer_http_error_sanitized():
    svc = OpenAICompatibleService(
        "http://llm.test/v1", api_key="SECRET-KEY", http=_client(_llm_handler("x", status=500))
    )
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    msg = str(exc.value)
    assert "HTTP 500" in msg
    assert "llm.test" not in msg  # pas d'URL
    assert "SECRET-KEY" not in msg  # pas de secret


async def test_infer_sends_bearer_and_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    svc = OpenAICompatibleService("http://llm.test/v1", api_key="K", http=_client(handler))
    await svc.infer("Kev-4B", "SYS", "USR")
    assert captured["auth"] == "Bearer K"
    assert captured["body"]["model"] == "Kev-4B"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "USR"}
    assert captured["body"]["temperature"] == 0.0


async def test_make_inference_service_from_settings(session):
    from app.settings.service import set_setting

    await set_setting(session, "llm_api_base_url", "http://custom.test/v1")
    await set_setting(session, "llm_api_key", "DB-KEY")
    svc = await make_inference_service(session)
    assert svc._base_url == "http://custom.test/v1"
    assert svc._api_key == "DB-KEY"


async def test_make_inference_service_defaults(session):
    svc = await make_inference_service(session)
    assert svc._base_url == "http://localhost:11434/v1"
    assert svc._api_key is None


# --- Timeout paramétrable + warm-up ---


async def test_make_inference_service_timeout_from_settings(session):
    from app.settings.service import set_setting

    await set_setting(session, "inference_timeout", "300")
    svc = await make_inference_service(session)
    assert svc._timeout == 300.0


async def test_make_inference_service_timeout_invalid_falls_back(session):
    from app.settings.service import set_setting

    await set_setting(session, "inference_timeout", "pas-un-nombre")
    svc = await make_inference_service(session)
    assert svc._timeout == 120.0


async def test_warmup_calls_infer_with_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(handler))
    await warmup(svc, "qwen3:4b")
    assert captured["body"]["model"] == "qwen3:4b"


async def test_warmup_absorbs_failure():
    """Un échec de warmup (timeout cold start, modèle absent) ne doit pas lever."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(handler))
    await warmup(svc, "qwen3:4b")  # ne lève pas


# --- Corps de requête paramétrable (llm_extra_body) ---


def _capture_client():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    return captured, _client(handler)


async def test_infer_no_extra_body_unchanged():
    """Verrou de régression : sans extra_body, le corps est exactement le corps fixe."""
    captured, client = _capture_client()
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    await svc.infer("Kev-4B", "SYS", "USR")
    assert captured["body"] == {
        "model": "Kev-4B",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }


async def test_infer_with_extra_body_merged():
    captured, client = _capture_client()
    svc = OpenAICompatibleService(
        "http://llm.test/v1",
        http=client,
        extra_body={"think": False, "options": {"num_ctx": 16384}},
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    body = captured["body"]
    assert body["think"] is False
    assert body["options"] == {"num_ctx": 16384}
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}


async def test_infer_extra_body_cannot_override_fixed_keys():
    captured, client = _capture_client()
    svc = OpenAICompatibleService(
        "http://llm.test/v1", http=client, extra_body={"temperature": 1.0, "model": "autre"}
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["model"] == "Kev-4B"


async def test_make_service_applies_extra_body_from_setting(session):
    """Service par défaut (backends locaux) : corps additionnel toujours chargé (§2)."""
    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    svc = await make_inference_service(session)
    assert svc._extra_body == {"think": False, "options": {"num_ctx": 8192}}


async def test_infer_with_openai_api_never_sends_extra_body(session, monkeypatch):
    """OPENAI_API : service propre au modèle, jamais le corps additionnel des backends locaux."""
    from types import SimpleNamespace

    from app.inference import infer_with
    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    captured, client = _capture_client()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=client._transport, **kw))
    default = await make_inference_service(session)
    model = SimpleNamespace(
        backend_type="OPENAI_API",
        model_identifier="https://api.x/v1#gpt-4o",
        api_key_enc=None,
        owner_id=None,
    )
    await infer_with(model, default, "SYS", "USR")
    assert "options" not in captured["body"] and "think" not in captured["body"]


async def test_make_service_invalid_extra_body_is_empty(session):
    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", "pas-un-json")
    svc = await make_inference_service(session)
    assert svc._extra_body == {}


async def test_make_service_null_extra_body_is_empty(session):
    svc = await make_inference_service(session)
    assert svc._extra_body == {}


# --- Mode natif Ollama (endpoint /api/chat) ---


def _native_handler(content: str, status: int = 200):
    """MockTransport : renvoie une réponse /api/chat (natif Ollama) avec `content`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": content}, "done": True},
        )

    return handler


def _capture_client_native(content: str = "{}"):
    """Capture URL + header Authorization + corps ; réponse /api/chat avec `content`."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": content}, "done": True},
        )

    return captured, _client(handler)


async def test_infer_native_ollama_hits_api_chat():
    captured, client = _capture_client_native("{}")
    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=client)
    await svc.infer("Kev-4B", "sys", "user")
    assert captured["url"] == "http://llm.test/api/chat"
    assert "/v1" not in captured["url"]


async def test_infer_native_body_exact_without_extra_body():
    """Verrou de régression : sans extra_body, le corps natif est exactement le corps fixe."""
    captured, client = _capture_client_native()
    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=client)
    await svc.infer("Kev-4B", "SYS", "USR")
    assert captured["body"] == {
        "model": "Kev-4B",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ],
        "stream": False,
        "format": "json",
        # natif Ollama : temperature lue dans options (ignorée en racine) — spec 2026-10-29 §N1
        "options": {"temperature": 0.0},
    }
    assert "response_format" not in captured["body"]


async def test_infer_native_body_merges_extra_body_options_think():
    captured, client = _capture_client_native()
    svc = OpenAICompatibleService(
        "http://llm.test/v1",
        native_ollama=True,
        http=client,
        extra_body={"think": False, "options": {"num_ctx": 16384}},
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    body = captured["body"]
    assert body["options"] == {"num_ctx": 16384, "temperature": 0.0}  # options fusionnées
    assert body["think"] is False
    assert body["format"] == "json"


@pytest.mark.parametrize("bad", [None, 5, [1], "x"])
async def test_infer_native_non_dict_options_ignored(bad):
    """``options`` non-dict dans le corps additionnel : ignoré, pas de TypeError."""
    captured, client = _capture_client_native()
    svc = OpenAICompatibleService(
        "http://llm.test/v1", native_ollama=True, http=client, extra_body={"options": bad}
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    assert captured["body"]["options"] == {"temperature": 0.0}


async def test_infer_native_extra_body_cannot_override_fixed_keys():
    captured, client = _capture_client_native()
    svc = OpenAICompatibleService(
        "http://llm.test/v1",
        native_ollama=True,
        http=client,
        extra_body={
            "options": {"temperature": 1.0, "num_ctx": 4096},
            "stream": True,
            "format": "text",
            "model": "autre",
        },
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    body = captured["body"]
    assert body["options"] == {"temperature": 0.0, "num_ctx": 4096}  # temperature fixe
    assert body["stream"] is False
    assert body["format"] == "json"
    assert body["model"] == "Kev-4B"


async def test_infer_native_returns_parsed_json():
    svc = OpenAICompatibleService(
        "http://llm.test/v1", native_ollama=True, http=_client(_native_handler(VALID_JSON))
    )
    result = await svc.infer("Kev-4B", "sys", "user")
    assert result["use_ai_behavior"] is True
    assert result["confidence_score"] == 0.9


async def test_infer_native_invalid_json_raises():
    svc = OpenAICompatibleService(
        "http://llm.test/v1", native_ollama=True, http=_client(_native_handler("not json"))
    )
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "invalid_json" in str(exc.value)


async def test_infer_native_missing_message_raises_invalid_response():
    for bad in ({"done": True}, {"message": {}}):

        def handler(request: httpx.Request, _bad=bad) -> httpx.Response:
            return httpx.Response(200, json=_bad)

        svc = OpenAICompatibleService(
            "http://llm.test/v1", native_ollama=True, http=_client(handler)
        )
        with pytest.raises(InferenceError) as exc:
            await svc.infer("Kev-4B", "sys", "user")
        assert "invalid_response" in str(exc.value)
        assert "invalid_json" not in str(exc.value)


async def test_infer_native_http_error_sanitized():
    svc = OpenAICompatibleService(
        "http://llm.test/v1",
        api_key="SECRET-KEY",
        native_ollama=True,
        http=_client(_native_handler("x", status=500)),
    )
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    msg = str(exc.value)
    assert "HTTP 500" in msg
    assert "llm.test" not in msg  # pas d'URL
    assert "SECRET-KEY" not in msg  # pas de secret


async def test_infer_native_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=_client(handler))
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "timeout" in str(exc.value)


async def test_infer_native_connection_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=_client(handler))
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "connection_error" in str(exc.value)


async def test_infer_native_sends_bearer_header():
    captured, client = _capture_client_native("{}")
    svc = OpenAICompatibleService(
        "http://llm.test/v1", api_key="K", native_ollama=True, http=client
    )
    await svc.infer("Kev-4B", "sys", "user")
    assert captured["auth"] == "Bearer K"


async def test_infer_native_root_without_v1_suffix():
    captured, client = _capture_client_native("{}")
    svc = OpenAICompatibleService("http://llm.test:11434", native_ollama=True, http=client)
    await svc.infer("Kev-4B", "sys", "user")
    assert captured["url"] == "http://llm.test:11434/api/chat"


async def test_infer_native_root_with_trailing_slash():
    captured, client = _capture_client_native("{}")
    svc = OpenAICompatibleService("http://llm.test/v1/", native_ollama=True, http=client)
    await svc.infer("Kev-4B", "sys", "user")
    assert captured["url"] == "http://llm.test/api/chat"


async def test_warmup_native_ollama_uses_api_chat():
    captured, client = _capture_client_native("{}")
    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=client)
    await warmup(svc, "qwen3:4b")
    assert captured["url"] == "http://llm.test/api/chat"
    assert captured["body"]["model"] == "qwen3:4b"


# --- Fabrique : flag natif (OLLAMA local → /api/chat) ---


async def test_make_service_default_native_flag_true(session):
    """Service par défaut (fabrique, sans modèle) = Ollama local → mode natif ; la décision
    par backend se fait par appel dans ``infer_with`` (spec 2026-10-29 §2)."""
    svc = await make_inference_service(session)
    assert svc._native_ollama is True


# --- Décision endpoint natif / /v1 PAR APPEL dans infer_with (spec 2026-10-29 §2) ---


def _capture_both():
    """Journalise (chemin, corps) de chaque appel ; répond aux deux formats (/api/chat et /v1)."""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "{}"},
                "choices": [{"message": {"content": "{}"}}],
            },
        )

    return calls, _client(handler)


async def test_infer_with_decides_endpoint_per_model_on_shared_default(session):
    """Même service par défaut : LLAMA_CPP → /v1/chat/completions (+ corps additionnel),
    OLLAMA → /api/chat ; le premier appel ne modifie pas le service partagé."""
    from types import SimpleNamespace

    from app.inference import infer_with
    from app.settings.service import set_setting

    await set_setting(session, "llm_api_base_url", "http://llm.test/v1")
    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    calls, client = _capture_both()
    default = await make_inference_service(session, http=client)
    llama = SimpleNamespace(backend_type="LLAMA_CPP", model_identifier="llama-big", owner_id=None)
    ollama = SimpleNamespace(backend_type="OLLAMA", model_identifier="qwen3:4b", owner_id=None)
    await infer_with(llama, default, "S", "U")
    await infer_with(ollama, default, "S", "U")
    assert [p for p, _ in calls] == ["/v1/chat/completions", "/api/chat"]
    v1_body = calls[0][1]
    assert v1_body["model"] == "llama-big"
    assert v1_body["options"] == {"num_ctx": 8192}  # backend local : corps additionnel appliqué
    assert v1_body["response_format"] == {"type": "json_object"}


# --- Timeout : valeur finie et > 0, sinon défaut 120 s (spec 2026-10-29 §3) ---


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-5"])
async def test_make_inference_service_timeout_non_finite_or_non_positive_falls_back(session, raw):
    from app.settings.service import set_setting

    await set_setting(session, "inference_timeout", raw)
    svc = await make_inference_service(session)
    assert svc._timeout == 120.0


# --- Réponse 200 inexploitable → InferenceError("invalid_response") (spec 2026-10-29 §N2) ---


@pytest.mark.parametrize(
    "native, response",
    [
        (False, httpx.Response(200, text="<html>proxy</html>")),
        (False, httpx.Response(200, json={"object": "error", "message": "boom"})),
        (True, httpx.Response(200, text="<html>proxy</html>")),
    ],
    ids=["v1-html", "v1-sans-choices", "natif-html"],
)
async def test_infer_unusable_200_raises_invalid_response(native, response):
    svc = OpenAICompatibleService(
        "http://llm.test/v1", native_ollama=native, http=_client(lambda request: response)
    )
    with pytest.raises(InferenceError, match="^invalid_response$"):
        await svc.infer("m", "s", "u")


# =====================================================================================
# Traçabilité modèle + version (spec 2026-10-29-tracabilite-modele-version §1)
# =====================================================================================

DIGEST = "a1b2c3d4e5f6" + "0" * 52  # 64 hex, format de GET /api/tags
TAGS = {
    "models": [
        {
            "name": "qwen3:4b",
            "model": "qwen3:4b",
            "modified_at": "2026-10-01T08:06:48.6397-07:00",
            "size": 2497293931,
            "digest": DIGEST,
            "details": {
                "format": "gguf",
                "family": "qwen3",
                "parameter_size": "4.0B",
                "quantization_level": "Q4_K_M",
            },
        },
        {"name": "qwen3:latest", "model": "qwen3:latest", "digest": "f" * 64},
    ]
}


def _tags_client(response=None, exc: Exception | None = None):
    """MockTransport : GET /api/tags → ``response`` (ou lève ``exc``) ; journalise les chemins."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if exc is not None:
            raise exc
        return response if response is not None else httpx.Response(200, json=TAGS)

    return paths, _client(handler)


def _model(backend, identifier, name="Mdl"):
    from types import SimpleNamespace

    return SimpleNamespace(
        name=name,
        backend_type=backend,
        model_identifier=identifier,
        api_key_enc=None,
        owner_id=None,
    )


async def test_model_snapshot_ollama_reads_digest_and_details_from_api_tags():
    """OLLAMA : version = digest des poids lu sur /api/tags (racine sans /v1), + détails."""
    from app.inference import model_snapshot

    paths, client = _tags_client()
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, native_ollama=True)
    snap = await model_snapshot(_model("OLLAMA", "qwen3:4b", "Qwen"), svc)
    assert paths == ["/api/tags"]
    assert snap["name"] == "Qwen" and snap["backend"] == "OLLAMA"
    assert snap["identifier"] == "qwen3:4b" and snap["version"] == DIGEST
    assert snap["parameter_size"] == "4.0B" and snap["quantization"] == "Q4_K_M"
    assert snap["family"] == "qwen3" and snap["format"] == "gguf"
    assert snap["size"] == 2497293931 and snap["modified_at"].startswith("2026-10-01")


async def test_model_snapshot_ollama_matches_implicit_latest_tag():
    """« qwen3 » == « qwen3:latest » pour Ollama (même normalisation que DELETE /models)."""
    from app.inference import model_snapshot

    _, client = _tags_client()
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, native_ollama=True)
    snap = await model_snapshot(_model("OLLAMA", "qwen3"), svc)
    assert snap["version"] == "f" * 64


@pytest.mark.parametrize(
    "response, exc",
    [
        (httpx.Response(500, text="boom"), None),
        (httpx.Response(200, text="<html>pas du json</html>"), None),
        (None, httpx.ConnectError("refused")),
        (httpx.Response(200, json={"models": [{"name": "autre:1b", "digest": "e" * 64}]}), None),
        (httpx.Response(200, json={"models": [{"name": "qwen3:4b", "digest": "zz;rm -rf"}]}), None),
    ],
    ids=["http-500", "non-json", "connection-error", "tag-absent", "bad-digest"],
)
async def test_model_snapshot_never_raises(response, exc):
    """Relevé best-effort : tout échec → version None, identifiant conservé, aucune exception."""
    from app.inference import model_snapshot

    _, client = _tags_client(response, exc)
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, native_ollama=True)
    snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), svc)
    assert snap["identifier"] == "qwen3:4b" and snap["version"] is None


async def test_model_snapshot_without_http_service_is_best_effort():
    """Service de test (sans base_url ni client HTTP) : pas d'appel, version None."""
    from app.inference import model_snapshot

    class Fake:
        async def infer(self, model, system_prompt, user_prompt):
            return {}

    snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), Fake())
    assert snap["identifier"] == "qwen3:4b" and snap["version"] is None
    # modèle absent du registre : le nom du job sert d'identifiant (comme infer_with)
    snap = await model_snapshot(None, Fake(), name="Kev-4B")
    assert snap["name"] == "Kev-4B" and snap["identifier"] == "Kev-4B"
    assert await model_snapshot(None, Fake()) is None


async def test_model_snapshot_openai_api_hides_url_secrets_and_makes_no_call():
    """OPENAI_API : partie modèle + endpoint scheme://hôte:port ; ni userinfo, ni chemin, ni
    query, ni clé ; aucun appel réseau (l'écho de la réponse donnera la version)."""
    from app.inference import model_snapshot

    paths, client = _tags_client()
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, native_ollama=True)
    ident = "https://alice:pw-SECRET@api.x.test:8443/v1/proxy?key=SECRET-Q#gpt-4o"
    snap = await model_snapshot(_model("OPENAI_API", ident, "GPT"), svc)
    assert paths == []
    assert snap["backend"] == "OPENAI_API" and snap["identifier"] == "gpt-4o"
    assert snap["endpoint"] == "https://api.x.test:8443" and snap["version"] is None
    dumped = json.dumps(snap)
    assert "SECRET" not in dumped and "alice" not in dumped and "/proxy" not in dumped


def _echo_client(native: bool, model_field):
    """Réponse 200 (format natif ou /v1) dont le champ ``model`` vaut ``model_field``."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            {"message": {"role": "assistant", "content": "{}"}}
            if native
            else {"choices": [{"message": {"content": "{}"}}]}
        )
        if model_field is not None:
            body["model"] = model_field
        return httpx.Response(200, json=body)

    return _client(handler)


@pytest.mark.parametrize(
    "backend, native, echoed, expected",
    [
        ("LLAMA_CPP", False, "llama-big-Q4_K_M.gguf", "llama-big-Q4_K_M.gguf"),
        ("OLLAMA", True, "qwen3:4b", "qwen3:4b"),
        ("LLAMA_CPP", False, None, None),
        ("LLAMA_CPP", False, "v1\x00\n\x1b-" + "x" * 300, "v1-" + "x" * 197),
    ],
    ids=["v1-echo", "native-echo", "no-model-field", "control-chars"],
)
async def test_infer_with_meta_reports_served_model(backend, native, echoed, expected):
    """``meta["served_model"]`` = champ ``model`` de la réponse du service réellement appelé
    (copie /v1 comprise), assaini (caractères de contrôle retirés, 200 caractères max)."""
    from app.inference import infer_with

    default = OpenAICompatibleService(
        "http://llm.test/v1", http=_echo_client(native, echoed), native_ollama=True
    )
    meta: dict = {}
    await infer_with(_model(backend, "m"), default, "S", "U", meta=meta)
    assert meta["served_model"] == expected


async def test_infer_with_meta_openai_api_served_model(monkeypatch):
    """OPENAI_API (service propre au modèle) : l'alias résolu renvoyé par l'API est remonté."""
    from app.inference import infer_with

    real = httpx.AsyncClient
    echo = _echo_client(False, "gpt-4o-2024-08-06")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=echo._transport, **kw))
    default = OpenAICompatibleService("http://llm.test/v1", native_ollama=True)
    meta: dict = {}
    await infer_with(_model("OPENAI_API", "https://api.x/v1#gpt-4o"), default, "S", "U", meta=meta)
    assert meta["served_model"] == "gpt-4o-2024-08-06"


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("https://api.x.test/v1#gpt-4o", ("gpt-4o", "https://api.x.test")),
        ("https://bob:pw@api.x.test:8443/v1?key=K#gpt-4o", ("gpt-4o", "https://api.x.test:8443")),
        ("https://api.x.test/v1", (None, "https://api.x.test")),  # sans « # » : pas de modèle
        ("pas-une-url", (None, None)),  # ni « # » ni URL : jamais la chaîne entière
        ("https://api.x.test:99999/v1#m", ("m", None)),  # port invalide
        ("https://api.x.test:abc/v1#m", ("m", None)),
        ("http://[::1]:8080/v1#m", ("m", "http://[::1]:8080")),  # IPv6 : crochets conservés
        (
            "https://api.x.test/v1#org/model#2",
            ("org/model#2", "https://api.x.test"),
        ),  # « # » du nom
    ],
)
def test_public_model_ref_openai_api(identifier, expected):
    from app.inference import public_model_ref

    assert public_model_ref("OPENAI_API", identifier) == expected


def test_public_model_ref_other_backends_keep_identifier():
    from app.inference import public_model_ref

    assert public_model_ref("OLLAMA", "qwen3:4b") == ("qwen3:4b", None)
    assert public_model_ref(None, "qwen3:4b") == ("qwen3:4b", None)


async def test_model_snapshot_failure_log_hides_url_and_secrets(caplog):
    """Hygiène des logs : un relevé en échec ne journalise ni userinfo ni hôte du service."""
    import logging

    from app.inference import model_snapshot

    _, client = _tags_client(exc=httpx.ConnectError("refused http://u:SECRET@llm.test/api/tags"))
    svc = OpenAICompatibleService("http://u:SECRET@llm.test/v1", http=client, native_ollama=True)
    with caplog.at_level(logging.DEBUG):
        snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), svc)
    assert snap["version"] is None
    assert "model_snapshot" in caplog.text  # le warning est bien émis
    assert "SECRET" not in caplog.text and "llm.test" not in caplog.text
