"""Tests for the InferenceService (app/inference) — 100% offline (mocked LLM)."""

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
    """MockTransport: returns a /chat/completions response with `content`."""

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


async def test_infer_provider_error_in_content_raises():
    # proxy returns 200 but the content is the upstream error JSON
    err = json.dumps(
        {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "temporarily blocked"}}
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(err)))
    with pytest.raises(InferenceError) as exc:
        await svc.infer("Kev-4B", "sys", "user")
    assert "provider_error_FAILED_PRECONDITION" in str(exc.value)


async def test_infer_error_field_string_not_misdetected():
    # a legitimate answer whose extracted field is named "error" (string value)
    ok = json.dumps({"error": "texte extrait", "confidence_score": 0.5})
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(ok)))
    result = await svc.infer("Kev-4B", "sys", "user")
    assert result["error"] == "texte extrait"


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


# --- Configurable timeout + warm-up ---


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
    """A warmup failure (cold start timeout, missing model) must not raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(handler))
    await warmup(svc, "qwen3:4b")  # does not raise


# --- Configurable request body (llm_extra_body) ---


def _capture_client():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    return captured, _client(handler)


async def test_infer_no_extra_body_unchanged():
    """Regression lock: without extra_body, the body is exactly the fixed body."""
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
    """Default service (local backends): extra body always loaded (§2)."""
    from app.settings.service import set_setting

    await set_setting(session, "llm_extra_body", '{"think": false, "options": {"num_ctx": 8192}}')
    svc = await make_inference_service(session)
    assert svc._extra_body == {"think": False, "options": {"num_ctx": 8192}}


async def test_infer_with_openai_api_never_sends_extra_body(session, monkeypatch):
    """OPENAI_API: model-specific service, never the extra body of the local backends."""
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
    """MockTransport: returns an /api/chat (Ollama native) response with `content`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": content}, "done": True},
        )

    return handler


def _capture_client_native(content: str = "{}"):
    """Captures URL + Authorization header + body; /api/chat response with `content`."""
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
    """Regression lock: without extra_body, the native body is exactly the fixed body."""
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
        # Ollama native: temperature read in options (ignored at root) — spec 2026-10-29 §N1
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
    assert body["options"] == {"num_ctx": 16384, "temperature": 0.0}  # options merged
    assert body["think"] is False
    assert body["format"] == "json"


@pytest.mark.parametrize("bad", [None, 5, [1], "x"])
async def test_infer_native_non_dict_options_ignored(bad):
    """Non-dict ``options`` in the extra body: ignored, no TypeError."""
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
    """Default service (factory, no model) = local Ollama -> native mode; the per-backend
    decision is made per call in ``infer_with`` (spec 2026-10-29 §2)."""
    svc = await make_inference_service(session)
    assert svc._native_ollama is True


# --- Native endpoint / /v1 decision PER CALL in infer_with (spec 2026-10-29 §2) ---


def _capture_both():
    """Logs (path, body) of each call; answers both formats (/api/chat and /v1)."""
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
    """Same default service: LLAMA_CPP -> /v1/chat/completions (+ extra body),
    OLLAMA -> /api/chat; the first call does not modify the shared service."""
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
    assert v1_body["options"] == {"num_ctx": 8192}  # local backend: extra body applied
    assert v1_body["response_format"] == {"type": "json_object"}


async def test_infer_with_min_interval_between_calls(monkeypatch):
    """LLM_MIN_INTERVAL_SECONDS: minimum interval between two calls of the same model —
    provider rate limits (abuse detection) without changing the answers."""
    import time
    from types import SimpleNamespace

    import app.inference as inf

    monkeypatch.setattr(inf, "_MIN_INTERVAL", 0.3)
    inf._last_call.clear()
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(VALID_JSON)))
    model = SimpleNamespace(backend_type="OLLAMA", model_identifier="kef:4b", owner_id=None)
    t0 = time.monotonic()
    await inf.infer_with(model, svc, "SYS", "USR")
    await inf.infer_with(model, svc, "SYS", "USR")
    assert time.monotonic() - t0 >= 0.25  # ~0.3 s of spacing on the second call


async def test_infer_with_min_interval_per_model(monkeypatch):
    """The spacing is per model: two different models do not throttle each other."""
    import time
    from types import SimpleNamespace

    import app.inference as inf

    monkeypatch.setattr(inf, "_MIN_INTERVAL", 0.3)
    inf._last_call.clear()
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(VALID_JSON)))
    m1 = SimpleNamespace(backend_type="OLLAMA", model_identifier="kef:4b", owner_id=None)
    m2 = SimpleNamespace(backend_type="OLLAMA", model_identifier="qwen:4b", owner_id=None)
    t0 = time.monotonic()
    await inf.infer_with(m1, svc, "SYS", "USR")
    await inf.infer_with(m2, svc, "SYS", "USR")
    assert time.monotonic() - t0 < 0.25  # no spacing between the two models


async def test_infer_with_interval_disabled_by_default(monkeypatch):
    import time
    from types import SimpleNamespace

    import app.inference as inf

    monkeypatch.setattr(inf, "_MIN_INTERVAL", 0.0)
    inf._last_call.clear()
    svc = OpenAICompatibleService("http://llm.test/v1", http=_client(_llm_handler(VALID_JSON)))
    model = SimpleNamespace(backend_type="OLLAMA", model_identifier="kef:4b", owner_id=None)
    t0 = time.monotonic()
    await inf.infer_with(model, svc, "SYS", "USR")
    await inf.infer_with(model, svc, "SYS", "USR")
    assert time.monotonic() - t0 < 0.2


# --- Timeout: finite value and > 0, otherwise default 120 s (spec 2026-10-29 §3) ---


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-5"])
async def test_make_inference_service_timeout_non_finite_or_non_positive_falls_back(session, raw):
    from app.settings.service import set_setting

    await set_setting(session, "inference_timeout", raw)
    svc = await make_inference_service(session)
    assert svc._timeout == 120.0


# --- Unusable 200 response -> InferenceError("invalid_response") (spec 2026-10-29 §N2) ---


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
# Model traceability + version (spec 2026-10-29-tracabilite-modele-version §1)
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
    """MockTransport: GET /api/tags -> ``response`` (or raises ``exc``); logs the paths."""
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
    """OLLAMA: version = weights digest read from /api/tags (root without /v1), + details."""
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
    """ "qwen3" == "qwen3:latest" for Ollama (same normalization as DELETE /models)."""
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
    """Best-effort reading: any failure -> version None, identifier kept, no exception."""
    from app.inference import model_snapshot

    _, client = _tags_client(response, exc)
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, native_ollama=True)
    snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), svc)
    assert snap["identifier"] == "qwen3:4b" and snap["version"] is None


async def test_model_snapshot_without_http_service_is_best_effort():
    """Test service (no base_url nor HTTP client): no call, version None."""
    from app.inference import model_snapshot

    class Fake:
        async def infer(self, model, system_prompt, user_prompt):
            return {}

    snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), Fake())
    assert snap["identifier"] == "qwen3:4b" and snap["version"] is None
    # model absent from the registry: the job name serves as identifier (like infer_with)
    snap = await model_snapshot(None, Fake(), name="Kev-4B")
    assert snap["name"] == "Kev-4B" and snap["identifier"] == "Kev-4B"
    assert await model_snapshot(None, Fake()) is None


async def test_model_snapshot_openai_api_hides_url_secrets_and_makes_no_call():
    """OPENAI_API: model part + endpoint scheme://host:port; no userinfo, no path, no
    query, no key; no network call (the response echo will give the version)."""
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
    """200 response (native or /v1 format) whose ``model`` field equals ``model_field``."""

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
    """``meta["served_model"]`` = ``model`` field of the response from the service actually called
    (/v1 copies included), sanitized (control characters removed, 200 characters max)."""
    from app.inference import infer_with

    default = OpenAICompatibleService(
        "http://llm.test/v1", http=_echo_client(native, echoed), native_ollama=True
    )
    meta: dict = {}
    await infer_with(_model(backend, "m"), default, "S", "U", meta=meta)
    assert meta["served_model"] == expected


async def test_infer_with_meta_openai_api_served_model(monkeypatch):
    """OPENAI_API (model-specific service): the resolved alias returned by the API is surfaced."""
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
        ("https://api.x.test/v1", (None, "https://api.x.test")),  # no "#": no model
        ("pas-une-url", (None, None)),  # neither "#" nor a URL: never the whole string
        ("https://api.x.test:99999/v1#m", ("m", None)),  # port invalide
        ("https://api.x.test:abc/v1#m", ("m", None)),
        ("http://[::1]:8080/v1#m", ("m", "http://[::1]:8080")),  # IPv6: brackets kept
        (
            "https://api.x.test/v1#FRAG#gpt-4o",
            ("gpt-4o", "https://api.x.test"),
        ),  # inherited "#": the model is after the LAST one
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
    """Log hygiene: a failed reading logs neither userinfo nor service host."""
    import logging

    from app.inference import model_snapshot

    _, client = _tags_client(exc=httpx.ConnectError("refused http://u:SECRET@llm.test/api/tags"))
    svc = OpenAICompatibleService("http://u:SECRET@llm.test/v1", http=client, native_ollama=True)
    with caplog.at_level(logging.DEBUG):
        snap = await model_snapshot(_model("OLLAMA", "qwen3:4b"), svc)
    assert snap["version"] is None
    assert "model_snapshot" in caplog.text  # the warning is actually emitted
    assert "SECRET" not in caplog.text and "llm.test" not in caplog.text


# --- list_models (endpoint model picker, spec 2026-11-08) -----------------------------


async def test_list_models_parses_and_dedupes():
    """OpenAI-compatible ``GET {base_url}/models``: sorted, deduplicated ids."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"object": "list", "data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}
            )
        )
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    assert await svc.list_models() == ["a", "b"]


async def test_list_models_parses_ollama_shape():
    """Ollama's ``GET /v1/models`` returns ``{"models":[{"name":...}]}``, not
    OpenAI's ``{"data":[{"id":...}]}``. The picker must work on Ollama (the
    main RGPD-friendly local provider)."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:1.7b", "model": "qwen3:1.7b", "modified_at": "…"},
                        {"name": "llama3:latest", "model": "llama3:latest", "modified_at": "…"},
                    ]
                },
            )
        )
    )
    svc = OpenAICompatibleService("http://ollama.test/v1", http=client)
    assert await svc.list_models() == ["llama3:latest", "qwen3:1.7b"]


async def test_list_models_sends_bearer_and_models_path():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = OpenAICompatibleService("http://llm.test/v1", api_key="sek", http=client)
    assert await svc.list_models() == ["m"]
    assert seen["path"] == "/v1/models"
    assert seen["auth"] == "Bearer sek"


async def test_list_models_non_list_data_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": "nope"}))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError, match="invalid_response"):
        await svc.list_models()


async def test_list_models_invalid_json_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"oops"))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError, match="invalid_json"):
        await svc.list_models()


async def test_list_models_http_error_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "x"}))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError, match="HTTP 401"):
        await svc.list_models()


async def test_list_models_recursion_error_sanitized():
    """A deeply nested JSON body from the endpoint must not escape as a raw
    RecursionError (unhandled 500): it maps to the fixed invalid_response code."""
    body = b"[" * 100_000 + b"]" * 100_000
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError, match="invalid_response"):
        await svc.list_models()


async def test_list_models_body_capped():
    """The models-list body is untrusted and user-directed: an oversized body
    is rejected (invalid_response) instead of being fully parsed in memory."""
    body = b'{"data":[' + b'"x",' * 600_000 + b'"x"]}'  # ~2.4 MB > the 2 MiB budget
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError, match="invalid_response"):
        await svc.list_models()


async def test_list_models_strips_and_drops_blank_ids():
    """Blank or whitespace-only ids from the endpoint do not create dead
    registry rows: they are stripped and the empty ones dropped."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"data": [{"id": "  gpt-4o  "}, {"id": "   "}, {"id": ""}, {"id": "b"}]}
            )
        )
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    assert await svc.list_models() == ["b", "gpt-4o"]


async def test_list_models_rebind_protected_like_chat(monkeypatch):
    """The listing goes through the same public-URL check as the inference calls
    (pinned IP at call time, not only at registration)."""
    import app.model_access as ma

    monkeypatch.setattr(
        ma.socket,
        "getaddrinfo",
        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": "m"}]}))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client, public_only=True)
    assert await svc.list_models() == ["m"]

    monkeypatch.setattr(ma.socket, "getaddrinfo", lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", port))])
    svc2 = OpenAICompatibleService("http://llm.test/v1", public_only=True, timeout=0.5)
    with pytest.raises(InferenceError, match="url_not_allowed"):
        await svc2.list_models()
