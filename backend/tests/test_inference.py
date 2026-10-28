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


async def test_make_service_ollama_applies_extra_body_from_setting(session):
    from types import SimpleNamespace

    from app.settings.service import set_setting

    await set_setting(
        session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}'
    )
    model = SimpleNamespace(backend_type="OLLAMA")
    svc = await make_inference_service(session, model=model)
    assert svc._extra_body == {"think": False, "options": {"num_ctx": 8192}}


async def test_make_service_openai_api_never_applies_extra_body(session):
    from types import SimpleNamespace

    from app.settings.service import set_setting

    await set_setting(
        session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}'
    )
    model = SimpleNamespace(backend_type="OPENAI_API")
    svc = await make_inference_service(session, model=model)
    assert svc._extra_body == {}


async def test_make_service_invalid_extra_body_is_empty(session):
    from types import SimpleNamespace

    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", "pas-un-json")
    svc = await make_inference_service(session, model=SimpleNamespace(backend_type="OLLAMA"))
    assert svc._extra_body == {}


async def test_make_service_null_extra_body_is_empty(session):
    from types import SimpleNamespace

    svc = await make_inference_service(session, model=SimpleNamespace(backend_type="OLLAMA"))
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
        "temperature": 0.0,
        "format": "json",
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
    assert body["options"] == {"num_ctx": 16384}
    assert body["think"] is False
    assert body["format"] == "json"


async def test_infer_native_extra_body_cannot_override_fixed_keys():
    captured, client = _capture_client_native()
    svc = OpenAICompatibleService(
        "http://llm.test/v1",
        native_ollama=True,
        http=client,
        extra_body={"temperature": 1.0, "stream": True, "format": "text", "model": "autre"},
    )
    await svc.infer("Kev-4B", "SYS", "USR")
    body = captured["body"]
    assert body["temperature"] == 0.0
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


async def test_make_service_ollama_model_sets_native_flag(session):
    from types import SimpleNamespace

    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    svc = await make_inference_service(session, model=SimpleNamespace(backend_type="OLLAMA"))
    assert svc._native_ollama is True
    assert svc._extra_body == {"think": False, "options": {"num_ctx": 8192}}


async def test_make_service_openai_api_model_native_flag_false_and_no_extra_body(session):
    from types import SimpleNamespace

    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    svc = await make_inference_service(session, model=SimpleNamespace(backend_type="OPENAI_API"))
    assert svc._native_ollama is False
    assert svc._extra_body == {}


async def test_make_service_no_model_defaults_native_flag_true(session):
    """Service par défaut (fabrique sans modèle) = Ollama local → mode natif."""
    svc = await make_inference_service(session)
    assert svc._native_ollama is True


async def test_make_service_non_ollama_local_backend_native_flag_false(session):
    from types import SimpleNamespace

    svc = await make_inference_service(session, model=SimpleNamespace(backend_type="LLAMA_CPP"))
    assert svc._native_ollama is False
