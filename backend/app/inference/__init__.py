"""InferenceService — OpenAI-compatible ``/v1/chat/completions`` client (Module 3/4).

The wire format is standard JSON messages (exposed by Ollama / llama.cpp / vLLM).
The spec §5 ChatML format is the CONTENT of the prompts (system_prompt +
user_prompt with ``{methods_text}`` substituted), not the wire format.

Errors are sanitized (no URL or secret in the message), like ``NCBIError`` in
``app/pubmed``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import time
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx

from app.core.crypto import decrypt_secret
from app.settings.service import get_setting

logger = logging.getLogger(__name__)

# Minimum spacing between two calls of the same model (provider rate limits /
# abuse detection, which echo an error JSON in the content). Read at import:
# LLM_MIN_INTERVAL_SECONDS. ``_throttle`` enforces it per model.
_MIN_INTERVAL = float(os.environ.get("LLM_MIN_INTERVAL_SECONDS", "0") or 0)
_last_call: dict[str, float] = {}
_throttle_lock = asyncio.Lock()


async def _throttle(model_key: str) -> None:
    """Sleep so that two calls of the same model are spaced by at least
    ``_MIN_INTERVAL`` seconds. No-op when the interval is disabled (0)."""
    if _MIN_INTERVAL <= 0:
        return
    now = time.monotonic()
    async with _throttle_lock:
        last = _last_call.get(model_key, 0.0)
        wait = max(0.0, _MIN_INTERVAL - (now - last))
        _last_call[model_key] = now + wait
    if wait > 0:
        await asyncio.sleep(wait)


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DIGEST = re.compile(r"^(sha256:)?[0-9a-f]{64}$")


class InferenceError(Exception):
    """Sanitized inference error (short message, no URL or secret)."""


class OpenAICompatibleService:
    """Minimal ``/v1/chat/completions`` client (JSON messages, JSON response)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        http: httpx.AsyncClient | None = None,
        public_only: bool = False,
        extra_body: dict | None = None,
        native_ollama: bool = False,
    ) -> None:
        self._public_only = public_only  # user's model: never an internal address
        self._base_url = base_url.rstrip("/")
        self.base_url = self._base_url
        self._api_key = api_key
        self._timeout = timeout
        self._http = http  # injectable (mock in tests); otherwise created on demand
        self._extra_body = extra_body or {}  # local backends; the fixed keys take precedence
        # Ollama local : endpoint natif /api/chat (l'endpoint /v1 ignore options, ex. num_ctx).
        self._native_ollama = native_ollama
        # Model returned by the last response read (traceability of the served version).
        self.served_model: str | None = None

    async def infer(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        """Calls the model and returns the parsed JSON object (spec §5).

        Raises ``InferenceError`` if the response is not valid JSON, on timeout
        or HTTP error (message without the URL).
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._native_ollama:
            # Native endpoint: root without the /v1 suffix; think at the root, sampling
            # parameters in ``options`` (temperature at the root is ignored),
            # format "json" (not response_format, unknown to the native one).
            url = f"{self._base_url.removesuffix('/v1')}/api/chat"
            opts = self._extra_body.get("options")
            opts = opts if isinstance(opts, dict) else {}  # free-form setting: no TypeError
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
                # fixed temperature: wins over the setting; num_ctx & co. kept
                "options": {**opts, "temperature": 0.0},
            }
        else:
            url = f"{self._base_url}/chat/completions"
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            }
        body = {**self._extra_body, **body}  # fixed body: never overridden by the setting
        if self._public_only:
            # Re-verified at EVERY call (not only at registration): a later DNS
            # rewrite no longer leads to the internal network.
            # ponytail: resolution→connection window not closed; pin the IP if needed.
            from app.model_access import check_public_url

            try:
                await asyncio.to_thread(check_public_url, self._base_url)
            except ValueError as exc:
                raise InferenceError("url_not_allowed") from exc
        owned = self._http is None
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                resp = await client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise InferenceError("timeout") from exc
            except httpx.HTTPError as exc:
                raise InferenceError("connection_error") from exc
            if resp.status_code >= 400:
                raise InferenceError(f"HTTP {resp.status_code}")
            try:
                data = resp.json()
                if self._native_ollama:
                    content = data["message"]["content"]
                else:
                    content = data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise InferenceError("invalid_response") from exc
            self.served_model = clean_meta(data.get("model"))
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError) as exc:
                raise InferenceError("invalid_json") from exc
            code = provider_error_code(parsed)
            if code:
                raise InferenceError(code)
            return parsed
        finally:
            if owned:
                await client.aclose()

    async def chat_stream(self, model: str, messages: list[dict]) -> AsyncIterator[str]:
        """Streams a free-text answer (chat, spec 2026-11-07-chat-rag §2).

        No JSON contract: the caller sends full messages and receives text
        chunks. Ollama native: NDJSON lines (``message.content``,
        ``done: true`` at the end); OpenAI-compatible: SSE lines
        (``choices[0].delta.content``, ``data: [DONE]`` at the end).
        Raises ``InferenceError`` on timeout / HTTP >= 400 / an unreadable
        line (the endpoint translates it into an SSE ``error`` event).
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._native_ollama:
            url = f"{self._base_url.removesuffix('/v1')}/api/chat"
            body = {"model": model, "messages": messages, "stream": True}
        else:
            url = f"{self._base_url}/chat/completions"
            body = {"model": model, "messages": messages, "stream": True, "temperature": 0.0}
        body = {**self._extra_body, **body}
        if self._public_only:
            from app.model_access import check_public_url

            try:
                await asyncio.to_thread(check_public_url, self._base_url)
            except ValueError as exc:
                raise InferenceError("url_not_allowed") from exc
        owned = self._http is None
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise InferenceError(f"HTTP {resp.status_code}")
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if self._native_ollama:
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise InferenceError("invalid_response") from exc
                            chunk = (data.get("message") or {}).get("content")
                            if chunk:
                                yield chunk
                            if data.get("model"):
                                self.served_model = clean_meta(data.get("model"))
                            if data.get("done"):
                                break
                        else:
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:") :].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                data = json.loads(payload)
                                chunk = data["choices"][0]["delta"].get("content")
                            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                                raise InferenceError("invalid_response") from exc
                            if chunk:
                                yield chunk
            except httpx.TimeoutException as exc:
                raise InferenceError("timeout") from exc
            except httpx.HTTPError as exc:
                raise InferenceError("connection_error") from exc
        finally:
            if owned:
                await client.aclose()


def provider_error_code(obj) -> str | None:
    """Provider error echoed in the response content.

    Some proxies return HTTP 200 and embed the upstream error in the message
    content (rate limits, abuse detection, model routing). Returns a short
    error code, or ``None`` when this is a normal answer.
    """
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    if isinstance(err, dict) and "message" in err and ("code" in err or "status" in err):
        return f"provider_error_{err.get('status') or err.get('code')}"
    return None


def clean_meta(v, limit: int = 200) -> str | None:
    """String collected from a third party: ``str`` only, no control characters, truncated."""
    if not isinstance(v, str):
        return None
    return _CONTROL_CHARS.sub("", v).strip()[:limit] or None


def ollama_ref(identifier: str) -> str:
    """Normalized Ollama reference: implicit ":latest" tag if the last segment has none."""
    return identifier if ":" in identifier.rsplit("/", 1)[-1] else f"{identifier}:latest"


def public_model_ref(backend: str | None, identifier: str) -> tuple[str | None, str | None]:
    """Publishable (identifier, endpoint). ``OPENAI_API``: ``<url>#<model>`` → model part +
    ``scheme://host[:port]`` (never userinfo, path, query or fragment); else the identifier."""
    if backend != "OPENAI_API":
        return identifier, None
    # Inherited line with several "#": URL = before the FIRST one, model = after the LAST one
    # (a fragment must never become the model name).
    if "#" in identifier:
        url, name = identifier.split("#", 1)[0], identifier.rsplit("#", 1)[1]
    else:
        url, name = identifier, None
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme, parts.hostname, parts.port
    except ValueError:
        scheme = host = port = None
    if not scheme or not host:
        return name or None, None
    host = f"[{host}]" if ":" in host else host
    return name or None, f"{scheme}://{host}" + (f":{port}" if port else "")


async def model_snapshot(model, service, name: str | None = None) -> dict | None:
    """Snapshot of a registry model for traceability: ``{name, backend, identifier,
    version}`` (+ ``endpoint`` for OPENAI_API, Ollama details).

    Version: weights digest for Ollama (``GET /api/tags``, read once), else ``None``
    (the served version is read from the responses). Best-effort: never fails.
    # ponytail: digest read at job start; a re-pull mid-job is not detected.
    """
    if model is None and not name:
        return None
    backend = model.backend_type if model is not None else None
    ident = model.model_identifier if model is not None else (name or "")
    snap: dict = {
        "name": model.name if model is not None else name,
        "backend": backend,
        "identifier": ident,
        "version": None,
    }
    if backend == "OPENAI_API":
        snap["identifier"], snap["endpoint"] = public_model_ref(backend, ident)
        return snap
    if backend not in (None, "OLLAMA"):
        return snap
    try:
        base_url = getattr(service, "base_url", None)
        http = getattr(service, "_http", None)
        if not base_url:
            return snap
        owned = http is None
        client = http or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.get(f"{base_url.rstrip('/').removesuffix('/v1')}/api/tags")
        finally:
            if owned:
                await client.aclose()
        resp.raise_for_status()
        want = ollama_ref(ident)
        for entry in resp.json()["models"]:
            if want != ollama_ref(entry.get("name") or entry.get("model") or ""):
                continue
            digest = clean_meta(entry.get("digest"), 80)
            snap["version"] = digest if digest and _DIGEST.match(digest) else None
            details = entry.get("details") or {}
            extra = {
                "size": entry.get("size") if isinstance(entry.get("size"), int) else None,
                "modified_at": clean_meta(entry.get("modified_at"), 40),
                "parameter_size": clean_meta(details.get("parameter_size"), 40),
                "quantization": clean_meta(details.get("quantization_level"), 40),
                "family": clean_meta(details.get("family"), 40),
                "format": clean_meta(details.get("format"), 40),
            }
            snap.update({k: v for k, v in extra.items() if v is not None})
            break
    except Exception as exc:  # noqa: BLE001 - traceability must never fail a job
        logger.warning("model_snapshot %s : %s", ident, type(exc).__name__)
    return snap


async def unload_ollama(base_url: str, model: str, http: httpx.AsyncClient | None = None) -> None:
    """Frees an Ollama model's RAM (keep_alive=0); best-effort, never blocking."""
    root = base_url.rstrip("/").removesuffix("/v1")
    owned = http is None
    client = http or httpx.AsyncClient(timeout=10)
    try:
        await client.post(f"{root}/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        logger.info("ollama unload skipped for %s", model)
    finally:
        if owned:
            await client.aclose()


async def get_inference_timeout(db) -> float:
    """``inference_timeout`` (DB > env): finite value > 0, else 120 s (+ warning)."""
    raw = await get_setting(db, "inference_timeout")
    if raw:
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            timeout = 0.0
        if math.isfinite(timeout) and timeout > 0:
            return timeout
        logger.warning("inference_timeout invalide (%r) : 120 s", raw)
    return 120.0


def model_lock_key(backend_type: str, identifier: str) -> str:
    """Redis key of the per-model lock (one generation at a time per local model)."""
    return f"inference:lock:{backend_type}:{identifier}"


async def make_inference_service(
    db,
    http: httpx.AsyncClient | None = None,
) -> OpenAICompatibleService:
    """Builds the default service from system_settings (DB > env, encrypted secrets).

    ``llm_api_base_url`` (default ``http://localhost:11434/v1`` via ``DEFAULTS``),
    ``llm_api_key`` (secret, optional), ``inference_timeout`` (cf.
    ``get_inference_timeout``) and ``llm_extra_body`` (additional JSON body; None or
    invalid → empty body).

    This service only serves local backends (never an ``OPENAI_API`` model, which has
    its own service): native Ollama (``/api/chat``) by default; ``infer_with`` decides
    PER CALL (copy to ``/v1`` for llama.cpp, vLLM, …).
    """
    base_url = await get_setting(db, "llm_api_base_url")
    api_key = await get_setting(db, "llm_api_key")
    timeout = await get_inference_timeout(db)
    extra_body: dict = {}
    raw_body = await get_setting(db, "llm_extra_body")
    if raw_body:
        try:
            parsed = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            extra_body = parsed
        else:
            logger.warning("llm_extra_body invalide (%r) : corps vide", raw_body)
    return OpenAICompatibleService(
        base_url=base_url,
        api_key=api_key,
        http=http,
        timeout=timeout,
        extra_body=extra_body,
        native_ollama=True,
    )


async def warmup(
    service: OpenAICompatibleService,
    model_identifier: str,
    model=None,
) -> None:
    """Absorbs the cold start (weights loading) before the inference loop.

    A job's first real call pays for the model download/loading in Ollama;
    without this ping, the first article would exceed the timeout.
    Best-effort: a warmup failure must not kill the job (the first real call
    will raise the error, sanitized).
    """
    try:
        await infer_with(
            model,
            service,
            "You are an inference service.",
            'Reply only with {"ok": true}',
            name=model_identifier,
        )
    except InferenceError as exc:
        logger.warning("warmup %s : %s", model_identifier, exc)


async def infer_with(
    model,
    default: OpenAICompatibleService,
    system_prompt: str,
    user_prompt: str,
    name: str | None = None,
    meta: dict | None = None,
):
    """Calls a generative model from the registry.

    ``meta`` (optional) receives, after a successful call, ``served_model``: the ``model`` field
    returned by the service actually called (version traceability).

    ``OPENAI_API``: the model's own ``<url>#<model>`` identifier + encrypted key;
    else the default service (``llm_api_base_url`` setting): native ``/api/chat`` for
    Ollama (or no model), ``/v1`` for the other local backends (copy of the service).
    ``served_model`` is written on the default service (Ollama / no model): safe,
    because that service is built per job / request / test-bench run and used in sequence.
    """
    await _throttle(getattr(model, "model_identifier", None) or "default")
    if model is not None and model.backend_type == "OPENAI_API":
        url, _, name = model.model_identifier.partition("#")
        key = decrypt_secret(model.api_key_enc) if model.api_key_enc else None
        svc = OpenAICompatibleService(
            url, key, timeout=default._timeout, public_only=model.owner_id is not None
        )
        out = await svc.infer(name, system_prompt, user_prompt)
        _report_served(svc, meta)
        return out
    if model is None or model.backend_type == "OLLAMA":
        svc = default
    else:  # LLAMA_CPP, TRANSFORMERS, …: no /api/chat
        svc = copy.copy(default)
        svc._native_ollama = False
    ident = model.model_identifier if model is not None else (name or "")
    out = await svc.infer(ident, system_prompt, user_prompt)
    _report_served(svc, meta)
    return out


def _report_served(svc, meta: dict | None) -> None:
    if meta is not None:
        meta["served_model"] = clean_meta(getattr(svc, "served_model", None))


async def stream_with(
    model, default: OpenAICompatibleService, messages: list[dict]
) -> AsyncIterator[str]:
    """Streaming twin of ``infer_with`` (chat, spec 2026-11-07-chat-rag §2).

    Same model resolution (OPENAI_API: the model's own URL + key; OLLAMA: the
    default service); yields text chunks from ``chat_stream``.
    """
    await _throttle(getattr(model, "model_identifier", None) or "default")
    if model is not None and model.backend_type == "OPENAI_API":
        url, _, name = model.model_identifier.partition("#")
        key = decrypt_secret(model.api_key_enc) if model.api_key_enc else None
        svc = OpenAICompatibleService(
            url, key, timeout=default._timeout, public_only=model.owner_id is not None
        )
        async for chunk in svc.chat_stream(name, messages):
            yield chunk
        return
    if model is None or model.backend_type == "OLLAMA":
        svc = default
    else:  # LLAMA_CPP, TRANSFORMERS, …: no /api/chat
        svc = copy.copy(default)
        svc._native_ollama = False
    ident = model.model_identifier if model is not None else ""
    async for chunk in svc.chat_stream(ident, messages):
        yield chunk
