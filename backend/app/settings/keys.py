"""Known settings keys (Module 7): secret or not, and default values.

Secrets are encrypted AES-256-GCM in the database (``is_secret=True``); the other
values are stored as plain UTF-8 (``is_secret=False``).
"""

import os

#: Encrypted keys (never returned in clear to the API).
SECRET_KEYS: frozenset[str] = frozenset(
    {"ncbi_api_key", "llm_api_key", "smtp_password", "zotero_api_key", "openalex_api_key"}
)

#: Keys stored in clear (non-sensitive).
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

#: All keys accepted by the settings API.
KNOWN_KEYS: frozenset[str] = SECRET_KEYS | NON_SECRET_KEYS

#: Default values applied when the key is not defined in the database.
# LLM_API_BASE_URL (env): in Docker, point to the compose's "ollama" service.
# LLM_TIMEOUT_SECONDS (env): timeout per inference call — raise it on CPU
# (Ollama cold start: weights loading + prefill can exceed 120 s).
# LLM_EXTRA_BODY (env): additional JSON body of the LLM requests (e.g.
# {"think": false, "options": {"num_ctx": 16384}}), applied only to local
# backends; None or invalid → empty body. num_ctx to adjust per deployment
# (default 16384; 32768 workable on GPU) — Ollama does not signal prompt
# truncation, a sufficient num_ctx is the only countermeasure.
DEFAULTS: dict[str, str | None] = {
    "llm_api_base_url": os.environ.get("LLM_API_BASE_URL", "http://localhost:11434/v1"),
    "inference_timeout": os.environ.get("LLM_TIMEOUT_SECONDS", "120"),
    "llm_extra_body": os.environ.get("LLM_EXTRA_BODY"),
}


def is_secret(key: str) -> bool:
    """True if the key must be encrypted in the database."""
    return key in SECRET_KEYS
