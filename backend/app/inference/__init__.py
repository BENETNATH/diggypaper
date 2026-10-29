"""InferenceService — client OpenAI-compatible ``/v1/chat/completions`` (Module 3/4).

Le wire format est le JSON messages standard (Ollama / llama.cpp / vLLM l'exposent).
Le format ChatML de la spec §5 est le CONTENU des prompts (system_prompt +
user_prompt avec ``{methods_text}`` substitué), pas le format sur le fil.

Les erreurs sont assainies (pas d'URL ni de secret dans le message), comme
``NCBIError`` dans ``app/pubmed``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math

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
            # Endpoint natif : root sans le suffixe /v1 ; think en racine, paramètres
            # d'échantillonnage dans ``options`` (la température en racine est ignorée),
            # format "json" (pas response_format, inconnu du natif).
            url = f"{self._base_url.removesuffix('/v1')}/api/chat"
            opts = self._extra_body.get("options")
            opts = opts if isinstance(opts, dict) else {}  # réglage libre : pas de TypeError
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
                # température fixe : prime sur le réglage ; num_ctx & co. conservés
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
            try:
                data = resp.json()
                if self._native_ollama:
                    content = data["message"]["content"]
                else:
                    content = data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise InferenceError("invalid_response") from exc
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


async def get_inference_timeout(db) -> float:
    """``inference_timeout`` (DB > env) : valeur finie et > 0, sinon 120 s (+ warning)."""
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


async def make_inference_service(
    db,
    http: httpx.AsyncClient | None = None,
) -> OpenAICompatibleService:
    """Fabrique le service par défaut depuis system_settings (DB > env, secrets chiffrés).

    ``llm_api_base_url`` (défaut ``http://localhost:11434/v1`` via ``DEFAULTS``),
    ``llm_api_key`` (secret, optionnel), ``inference_timeout`` (cf.
    ``get_inference_timeout``) et ``llm_extra_body`` (corps JSON additionnel ; None ou
    invalide → corps vide).

    Ce service ne sert qu'aux backends locaux (jamais à un modèle ``OPENAI_API``, qui a son
    propre service) : Ollama natif (``/api/chat``) par défaut ; ``infer_with`` décide PAR
    APPEL (copie en ``/v1`` pour llama.cpp, vLLM, …).
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
    """Absorbe le cold start (chargement des poids) avant la boucle d'inférence.

    Le premier appel réel d'un job paie le téléchargement/chargement du modèle
    dans Ollama ; sans ce ping, c'est le premier article qui dépasse le timeout.
    Best-effort : un échec de warmup ne doit pas tuer le job (le premier appel
    réel relèvera l'erreur, assainie).
    """
    try:
        await infer_with(
            model,
            service,
            "Tu es un service d'inférence.",
            'Réponds uniquement {"ok": true}',
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
):
    """Appelle un modèle génératif du registre.

    ``OPENAI_API`` : identifiant ``<url>#<modèle>`` + clé chiffrée propres au modèle ;
    sinon le service par défaut (réglage ``llm_api_base_url``) : natif ``/api/chat`` pour
    Ollama (ou sans modèle), ``/v1`` pour les autres backends locaux (copie du service, le
    service partagé n'est jamais muté).
    """
    if model is not None and model.backend_type == "OPENAI_API":
        url, _, name = model.model_identifier.partition("#")
        key = decrypt_secret(model.api_key_enc) if model.api_key_enc else None
        svc = OpenAICompatibleService(
            url, key, timeout=default._timeout, public_only=model.owner_id is not None
        )
        return await svc.infer(name, system_prompt, user_prompt)
    if model is None or model.backend_type == "OLLAMA":
        svc = default
    else:  # LLAMA_CPP, TRANSFORMERS, … : pas d'/api/chat
        svc = copy.copy(default)
        svc._native_ollama = False
    ident = model.model_identifier if model is not None else (name or "")
    return await svc.infer(ident, system_prompt, user_prompt)
