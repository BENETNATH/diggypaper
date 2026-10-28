"""Flag de déploiement Ollama (env ``OLLAMA_MODE``) — docker | host | off.

Flag de déploiement lu UNE FOIS à l'import (pattern DEFAULTS de ``settings/keys.py``),
pas un setting DB : il varie avec le conteneur, pas à chaud. Valeur absente →
``docker`` (invariant : comportement actuel) ; invalide → ``docker`` + warning.
"""

import logging
import os

logger = logging.getLogger(__name__)

MODES: frozenset[str] = frozenset({"docker", "host", "off"})

#: Racine du conteneur Ollama du compose (sans /v1) — base résiduelle détectée en mode host.
COMPOSE_ROOT: str = "http://ollama:11434"


def resolve_mode() -> str:
    """Valeur normalisée de OLLAMA_MODE ; absente/empty/invalid → 'docker' (+ warning si invalide)."""
    raw = os.environ.get("OLLAMA_MODE", "").strip().lower()
    if raw in MODES:
        return raw
    if raw:  # vide ≠ invalide : pas de warning
        logger.warning("OLLAMA_MODE invalide (%r) : 'docker' appliqué", raw)
    return "docker"


#: Lu à l'import (pattern DEFAULTS, settings/keys.py) — tests : monkeypatcher ce module.
MODE: str = resolve_mode()
