"""Rate limiting in-memory par IP (F7) — anti brute-force sur login/register/invite.

Limiteur par fenêtre glissante de 15 min : dict ip → deque de timestamps (monotonic),
purge des entrées > 15 min. Dépasser la limite → 429 {"detail": "rate_limited"}.

Limite par endpoint (par IP) :
  - login    : 10 req / 15 min
  - register :  5 req / 15 min
  - invite   : 20 req / 15 min

NOTE (multi-workers) : l'état est in-memory par processus, donc la limite est
appliquée par worker. En production, la mitigation robuste est nginx limit_req
(ou un store partagé type Redis) ; ce limiteur est un filet de sécurité applicatif.
"""

import time
from collections import defaultdict, deque

WINDOW_SECONDS = 15 * 60

# ip → deque de timestamps (time.monotonic). Global par processus.
_buckets: dict[str, deque[float]] = defaultdict(deque)


def _purge(bucket: deque[float], now: float) -> None:
    """Retire les timestamps hors de la fenêtre de 15 min."""
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()


def is_rate_limited(key: str, limit: int, now: float | None = None) -> bool:
    """True si `key` a déjà ≥ `limit` requêtes dans la fenêtre de 15 min."""
    now = time.monotonic() if now is None else now
    bucket = _buckets[key]
    _purge(bucket, now)
    return len(bucket) >= limit


def record(key: str, now: float | None = None) -> None:
    """Enregistre une requête pour `key` (après purge de la fenêtre)."""
    now = time.monotonic() if now is None else now
    bucket = _buckets[key]
    _purge(bucket, now)
    bucket.append(now)


def reset() -> None:
    """Vide tous les compteurs (utilisé par les tests)."""
    _buckets.clear()
