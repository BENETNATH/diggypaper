"""Clés de settings connues (Module 7) : secret ou non, et valeurs par défaut.

Les secrets sont chiffrés AES-256-GCM en base (``is_secret=True``) ; les autres
valeurs sont stockées en clair UTF-8 (``is_secret=False``).
"""

import os

#: Clés chiffrées (jamais renvoyées en clair à l'API).
SECRET_KEYS: frozenset[str] = frozenset(
    {"ncbi_api_key", "llm_api_key", "smtp_password", "zotero_api_key", "openalex_api_key"}
)

#: Clés stockées en clair (non sensibles).
NON_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "llm_api_base_url",
        "inference_timeout",
        "llm_extra_body",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_from_email",
        "use_tls",
        "zotero_group_id",
        "contact_email",
    }
)

#: Toutes les clés acceptées par l'API settings.
KNOWN_KEYS: frozenset[str] = SECRET_KEYS | NON_SECRET_KEYS

#: Valeurs par défaut appliquées quand la clé n'est pas définie en base.
# LLM_API_BASE_URL (env) : en Docker, pointer vers le service « ollama » du compose.
# LLM_TIMEOUT_SECONDS (env) : timeout par appel d'inférence — à augmenter sur CPU
# (cold start Ollama : chargement des poids + prefill peuvent dépasser 120 s).
# LLM_EXTRA_BODY (env) : corps JSON additionnel des requêtes LLM (ex.
# {"think": false, "options": {"num_ctx": 16384}}), appliqué uniquement aux
# backends locaux ; None ou invalide → corps vide. num_ctx à ajuster par
# déploiement (16384 par défaut ; 32768 jouable sur GPU) — Ollama ne signale
# pas la troncature du prompt, un num_ctx suffisant est la seule contre-mesure.
DEFAULTS: dict[str, str | None] = {
    "llm_api_base_url": os.environ.get("LLM_API_BASE_URL", "http://localhost:11434/v1"),
    "inference_timeout": os.environ.get("LLM_TIMEOUT_SECONDS", "120"),
    "llm_extra_body": os.environ.get("LLM_EXTRA_BODY"),
}


def is_secret(key: str) -> bool:
    """True si la clé doit être chiffrée en base."""
    return key in SECRET_KEYS
