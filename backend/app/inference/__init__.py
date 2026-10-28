"""InferenceService — client OpenAI-compatible ``/v1/chat/completions`` (Module 3/4).

Le wire format est le JSON messages standard (Ollama / llama.cpp / vLLM l'exposent).
Le format ChatML de la spec §5 est le CONTENU des prompts (system_prompt +
user_prompt avec ``{methods_text}`` substitué), pas le format sur le fil.

Les erreurs sont assainies (pas d'URL ni de secret dans le message), comme
``NCBIError`` dans ``app/pubmed``.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.core.crypto import decrypt_secret
from app.settings.service import get_setting

logger = logging.getLogger(__name__)


class InferenceError(Exception):
    """Erreur d'inférence assainie (message court, sans URL ni secret)."""


class OpenAICompatibleService:
    """Client minimal ``/v1/chat/completions`` (JSON messages, réponse JSON)."""

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
        self._public_only = public_only  # modèle d'un utilisateur : jamais d'adresse interne
        self._base_url = base_url.rstrip("/")
        self.base_url = self._base_url
        self._api_key = api_key
        self._timeout = timeout
        self._http = http  # injectable (mock en tests) ; sinon créé à la demande
        self._extra_body = extra_body or {}  # backends locaux ; les clés fixes priment
        # Ollama local : endpoint natif /api/chat (l'endpoint /v1 ignore options, ex. num_ctx).
        self._native_ollama = native_ollama

    async def infer(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        """Appelle le modèle et renvoie l'objet JSON parsé (spec §5).

        Lève ``InferenceError`` si la réponse n'est pas un JSON valide, en cas de
        timeout ou d'erreur HTTP (message sans l'URL).
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._native_ollama:
            # Endpoint natif : root sans le suffixe /v1 ; options/think lues en racine,
            # format "json" (pas response_format, inconnu du natif).
            url = f"{self._base_url.removesuffix('/v1')}/api/chat"
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "temperature": 0.0,
                "format": "json",
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
        body = {**self._extra_body, **body}  # corps fixe : jamais écrasé par le réglage
        if self._public_only:
            # Revérifié à CHAQUE appel (pas seulement à l'enregistrement) : une réécriture DNS
            # après coup ne mène plus au réseau interne.
            # ponytail: fenêtre résolution→connexion non fermée ; épingler l'IP si besoin.
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
            data = resp.json()
            if self._native_ollama:
                try:
                    content = data["message"]["content"]
                except (KeyError, TypeError) as exc:
                    raise InferenceError("invalid_response") from exc
            else:
                content = data["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError) as exc:
                raise InferenceError("invalid_json") from exc
        finally:
            if owned:
                await client.aclose()


async def unload_ollama(base_url: str, model: str, http: httpx.AsyncClient | None = None) -> None:
    """Libère la RAM d'un modèle Ollama (keep_alive=0) ; best-effort, jamais bloquant."""
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


async def make_inference_service(
    db,
    http: httpx.AsyncClient | None = None,
    model=None,
) -> OpenAICompatibleService:
    """Fabrique le service depuis system_settings (DB > env, secrets chiffrés).

    ``llm_api_base_url`` (défaut ``http://localhost:11434/v1`` via ``DEFAULTS``),
    ``llm_api_key`` (secret, optionnel), ``inference_timeout`` (défaut
    ``LLM_TIMEOUT_SECONDS`` env ou 120 s) et ``llm_extra_body`` (corps JSON
    additionnel, appliqué uniquement aux backends locaux — jamais à un modèle
    ``OPENAI_API`` ; None ou invalide → corps vide).

    Mode natif Ollama (endpoint ``/api/chat``) : ``model is None`` (service par
    défaut = Ollama local) ou ``model.backend_type == "OLLAMA"`` ; les autres
    backends restent sur ``/v1/chat/completions``.
    """
    base_url = await get_setting(db, "llm_api_base_url")
    api_key = await get_setting(db, "llm_api_key")
    timeout = 120.0
    raw = await get_setting(db, "inference_timeout")
    if raw:
        try:
            timeout = float(raw)
        except ValueError:
            logger.warning("inference_timeout invalide (%r) : 120 s", raw)
    extra_body: dict = {}
    raw_body = await get_setting(db, "llm_extra_body")
    if raw_body and (model is None or model.backend_type != "OPENAI_API"):
        try:
            parsed = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            extra_body = parsed
        else:
            logger.warning("llm_extra_body invalide (%r) : corps vide", raw_body)
    # Ollama local (modèle OLLAMA, ou service par défaut sans modèle = Ollama) : endpoint natif
    # /api/chat. Les autres backends locaux (llama.cpp, vLLM, …) n'exposent pas /api/chat → /v1.
    native_ollama = model is None or model.backend_type == "OLLAMA"
    return OpenAICompatibleService(
        base_url=base_url,
        api_key=api_key,
        http=http,
        timeout=timeout,
        extra_body=extra_body,
        native_ollama=native_ollama,
    )


async def warmup(
    service: OpenAICompatibleService,
    model_identifier: str,
) -> None:
    """Absorbe le cold start (chargement des poids) avant la boucle d'inférence.

    Le premier appel réel d'un job paie le téléchargement/chargement du modèle
    dans Ollama ; sans ce ping, c'est le premier article qui dépasse le timeout.
    Best-effort : un échec de warmup ne doit pas tuer le job (le premier appel
    réel relèvera l'erreur, assainie).
    """
    try:
        await service.infer(
            model_identifier,
            "Tu es un service d'inférence.",
            'Réponds uniquement {"ok": true}',
        )
    except InferenceError as exc:
        logger.warning("warmup %s : %s", model_identifier, exc)


async def infer_with(
    model,
    default: OpenAICompatibleService,
    system_prompt: str,
    user_prompt: str,
    name: str | None = None,
):
    """Appelle un modèle génératif du registre.

    ``OPENAI_API`` : identifiant ``<url>#<modèle>`` + clé chiffrée propres au modèle ;
    sinon le service par défaut (Ollama / réglage ``llm_api_base_url``).
    """
    if model is not None and model.backend_type == "OPENAI_API":
        url, _, name = model.model_identifier.partition("#")
        key = decrypt_secret(model.api_key_enc) if model.api_key_enc else None
        svc = OpenAICompatibleService(
            url, key, timeout=default._timeout, public_only=model.owner_id is not None
        )
        return await svc.infer(name, system_prompt, user_prompt)
    ident = model.model_identifier if model is not None else (name or "")
    return await default.infer(ident, system_prompt, user_prompt)
