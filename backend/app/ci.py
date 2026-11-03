"""CI state of the running commit (public GitHub API, non-authenticated, cached).

The build injects ``COMMIT_SHA`` (docker compose build arg). ``ci_state`` queries
the repo's check runs for that SHA via the public GitHub API and reduces them to a
single state: green (all success), red (any failure/error), unknown (in progress,
empty, non-200, or dev build without a SHA). Results are cached per-SHA for a few
minutes to stay well under the unauthenticated rate limit (60 req/h).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

# Unauthenticated GitHub API: 60 req/h. Cache a few minutes per SHA.
CI_TTL_SECONDS = 300
_GITHUB_API_BASE = "https://api.github.com"
_DEFAULT_OWNER = "BENETNATH"
_DEFAULT_REPO = "diggypaper"


def repo() -> tuple[str, str]:
    """(owner, repo) of the public GitHub project (env-overridable)."""
    return (
        os.environ.get("GITHUB_REPO_OWNER", _DEFAULT_OWNER),
        os.environ.get("GITHUB_REPO_NAME", _DEFAULT_REPO),
    )


def commit_sha() -> str:
    """The build-time commit SHA (``unknown`` in dev / before a real build)."""
    return os.environ.get("COMMIT_SHA", "unknown")


@dataclass
class _Cache:
    # sha -> (monotonic timestamp, state dict)
    entries: dict[str, tuple[float, dict]] = field(default_factory=dict)

    def get(self, sha: str) -> dict | None:
        e = self.entries.get(sha)
        if e and time.monotonic() - e[0] < CI_TTL_SECONDS:
            return e[1]
        return None

    def set(self, sha: str, state: dict) -> None:
        self.entries[sha] = (time.monotonic(), state)


_CACHE = _Cache()


def _reduce(runs: list[dict]) -> str:
    """check-runs list -> 'green' | 'red' | 'unknown'."""
    if not runs:
        return "unknown"
    statuses = {r.get("status") for r in runs}
    if "in_progress" in statuses or "queued" in statuses:
        return "unknown"
    conclusions = {r.get("conclusion") for r in runs if r.get("conclusion")}
    if "failure" in conclusions or "error" in conclusions:
        return "red"
    if conclusions and all(c in ("success", "neutral") for c in conclusions):
        return "green"
    return "unknown"


def ci_state(sha: str, client: httpx.Client | None = None) -> dict:
    """CI state of ``sha``: ``{"state": "green"|"red"|"unknown", "sha": sha}``.

    Non-authenticated public GitHub API, cached per-SHA for ``CI_TTL_SECONDS`` s.
    ``client`` is injectable for tests (a real ``httpx.Client`` by default).
    """
    if not sha or sha == "unknown":
        return {"state": "unknown", "sha": sha}
    cached = _CACHE.get(sha)
    if cached is not None:
        return cached
    owner, name = repo()
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{name}/commits/{sha}/check-runs"
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=5)
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            state = {"state": "unknown", "sha": sha}
        else:
            state = {"state": _reduce(resp.json().get("check_runs", [])), "sha": sha}
    except Exception:
        state = {"state": "unknown", "sha": sha}
    finally:
        if owns_client:
            client.close()
    _CACHE.set(sha, state)
    return state


def _clear_cache() -> None:
    """Test helper: drop the per-SHA cache."""
    _CACHE.entries.clear()
