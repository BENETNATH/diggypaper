"""In-memory rate limiting per IP (F7) - anti brute-force on login/register/invite.

15-min sliding window limiter: dict ip -> deque of timestamps (monotonic),
purge of entries older than 15 min. Exceeding the limit -> 429 {"detail": "rate_limited"}.

Limit per endpoint (per IP):
  - login    : 10 req / 15 min
  - register :  5 req / 15 min
  - invite   : 20 req / 15 min

NOTE (multi-workers): state is in-memory per process, so the limit is
applied per worker. In production, the robust mitigation is nginx limit_req
(or a shared store like Redis); this limiter is an application-level safety net.
"""

import time
from collections import defaultdict, deque

WINDOW_SECONDS = 15 * 60

# ip -> deque of timestamps (time.monotonic). Global per process.
_buckets: dict[str, deque[float]] = defaultdict(deque)


def _purge(bucket: deque[float], now: float) -> None:
    """Removes timestamps outside the 15-min window."""
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()


def is_rate_limited(key: str, limit: int, now: float | None = None) -> bool:
    """True if `key` already has >= `limit` requests in the 15-min window."""
    now = time.monotonic() if now is None else now
    bucket = _buckets[key]
    _purge(bucket, now)
    return len(bucket) >= limit


def record(key: str, now: float | None = None) -> None:
    """Records a request for `key` (after purging the window)."""
    now = time.monotonic() if now is None else now
    bucket = _buckets[key]
    _purge(bucket, now)
    bucket.append(now)


def reset() -> None:
    """Clears all counters (used by the tests)."""
    _buckets.clear()
