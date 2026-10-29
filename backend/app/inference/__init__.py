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
import re
from urllib.parse import urlsplit

import httpx

from app.core.crypto import decrypt_secret
from app.settings.service import get_setting

logger = logging.getLogger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DIGEST = re.compile(r"^(sha256:)?[0-9a-f]{64}$")


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
        # Modèle renvoyé par la dernière réponse lue (traçabilité de la version servie).
        self.served_model: str | None = None

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
            self.served_model = clean_meta(data.get("model"))
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError) as exc:
                raise InferenceError("invalid_json") from exc
        finally:
            if owned:
                await client.aclose()


def clean_meta(v, limit: int = 200) -> str | None:
    """Chaîne relevée chez un tiers : ``str`` seulement, sans caractères de contrôle, tronquée."""
    if not isinstance(v, str):
        return None
    return _CONTROL_CHARS.sub("", v).strip()[:limit] or None


def ollama_ref(identifier: str) -> str:
    """Référence Ollama normalisée : tag « :latest » implicite si le dernier segment n'en a pas."""
    return identifier if ":" in identifier.rsplit("/", 1)[-1] else f"{identifier}:latest"


def public_model_ref(backend: str | None, identifier: str) -> tuple[str | None, str | None]:
    """(identifiant, endpoint) publiables. ``OPENAI_API`` : ``<url>#<modèle>`` → partie modèle +
    ``scheme://hôte[:port]`` (jamais userinfo, chemin, query ni fragment) ; sinon l'identifiant."""
    if backend != "OPENAI_API":
        return identifier, None
    url, _, name = identifier.partition("#")
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
    """Instantané d'un modèle du registre pour la traçabilité : ``{name, backend, identifier,
    version}`` (+ ``endpoint`` pour OPENAI_API, détails Ollama).

    Version : digest des poids pour Ollama (``GET /api/tags``, relevé une fois), sinon ``None``
    (la version servie est lue sur les réponses). Best-effort : n'échoue jamais.
    # ponytail: digest relevé au démarrage du job ; un re-pull en cours de job n'est pas détecté.
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
    except Exception as exc:  # noqa: BLE001 - la traçabilité ne doit jamais faire échouer un job
        logger.warning("model_snapshot %s : %s", ident, type(exc).__name__)
    return snap


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


def model_lock_key(backend_type: str, identifier: str) -> str:
    """Clé Redis du lock par modèle (une génération à la fois par modèle local)."""
    return f"inference:lock:{backend_type}:{identifier}"


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
    meta: dict | None = None,
):
    """Appelle un modèle génératif du registre.

    ``meta`` (optionnel) reçoit, après un appel réussi, ``served_model`` : le champ ``model``
    renvoyé par le service réellement appelé (traçabilité de la version).

    ``OPENAI_API`` : identifiant ``<url>#<modèle>`` + clé chiffrée propres au modèle ;
    sinon le service par défaut (réglage ``llm_api_base_url``) : natif ``/api/chat`` pour
    Ollama (ou sans modèle), ``/v1`` pour les autres backends locaux (copie du service).
    ``served_model`` est écrit sur le service par défaut (Ollama / sans modèle) : sans danger,
    car ce service est construit par job / requête / run de banc d'essai et utilisé en séquence.
    """
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
    else:  # LLAMA_CPP, TRANSFORMERS, … : pas d'/api/chat
        svc = copy.copy(default)
        svc._native_ollama = False
    ident = model.model_identifier if model is not None else (name or "")
    out = await svc.infer(ident, system_prompt, user_prompt)
    _report_served(svc, meta)
    return out


def _report_served(svc, meta: dict | None) -> None:
    if meta is not None:
        meta["served_model"] = clean_meta(getattr(svc, "served_model", None))
