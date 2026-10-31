"""Ollama deployment flag (env ``OLLAMA_MODE``) — docker | host | off.

Deployment flag read ONCE at import (the DEFAULTS pattern from ``settings/keys.py``),
not a DB setting: it varies with the container, not at runtime. Absent value →
``docker`` (invariant: current behavior); invalid → ``docker`` + warning.
"""

import logging
import os

logger = logging.getLogger(__name__)

MODES: frozenset[str] = frozenset({"docker", "host", "off"})

#: Compose's Ollama container root (without /v1) — residual base detected in host mode.
COMPOSE_ROOT: str = "http://ollama:11434"


def resolve_mode() -> str:
    """Normalized OLLAMA_MODE value; absent/empty/invalid → 'docker' (warning if invalid)."""
    raw = os.environ.get("OLLAMA_MODE", "").strip().lower()
    if raw in MODES:
        return raw
    if raw:  # vide ≠ invalide : pas de warning
        logger.warning("OLLAMA_MODE invalide (%r) : 'docker' appliqué", raw)
    return "docker"


#: Read at import (DEFAULTS pattern, settings/keys.py) — tests: monkeypatch this module.
MODE: str = resolve_mode()
