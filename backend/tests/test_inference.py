"""Tests de l'InferenceService (app/inference) — 100 % offline (LLM mocké)."""

import json

import httpx
import pytest

from app.inference import InferenceError, OpenAICompatibleService, make_inference_service


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
