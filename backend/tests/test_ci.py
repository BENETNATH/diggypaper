"""Tests for app/ci.py (CI state of the running commit) — 100% offline."""

import pytest

from app import ci


class FakeClient:
    """Minimal httpx.Client stand-in: records calls, returns canned check-runs."""

    def __init__(self, runs: list[dict] | None = None, status_code: int = 200):
        self._runs = runs if runs is not None else []
        self._status_code = status_code
        self.calls = 0

    def get(self, url):
        self.calls += 1

        class R:
            def __init__(self, c: "FakeClient"):
                self.status_code = c._status_code
                self._runs = c._runs

            def json(self):
                return {"check_runs": self._runs}

        return R(self)


class BoomClient:
    def get(self, url):
        raise ConnectionError("boom")


@pytest.fixture(autouse=True)
def _clear_ci_cache():
    ci._clear_cache()
    yield
    ci._clear_cache()


def test_ci_state_unknown_sha():
    assert ci.ci_state("unknown") == {"state": "unknown", "sha": "unknown"}
    assert ci.ci_state("") == {"state": "unknown", "sha": ""}


def test_ci_state_green():
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "success"},
    ]
    assert ci.ci_state("abc123", client=FakeClient(runs))["state"] == "green"


def test_ci_state_red_on_failure():
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ]
    assert ci.ci_state("abc123", client=FakeClient(runs))["state"] == "red"


def test_ci_state_red_on_error():
    runs = [{"status": "completed", "conclusion": "error"}]
    assert ci.ci_state("abc123", client=FakeClient(runs))["state"] == "red"


def test_ci_state_in_progress_is_unknown():
    runs = [
        {"status": "in_progress", "conclusion": None},
        {"status": "completed", "conclusion": "success"},
    ]
    assert ci.ci_state("abc123", client=FakeClient(runs))["state"] == "unknown"


def test_ci_state_no_runs_is_unknown():
    assert ci.ci_state("abc123", client=FakeClient([]))["state"] == "unknown"


def test_ci_state_non_200_is_unknown():
    assert ci.ci_state("abc123", client=FakeClient(status_code=404))["state"] == "unknown"


def test_ci_state_http_exception_is_unknown():
    assert ci.ci_state("abc123", client=BoomClient())["state"] == "unknown"


def test_ci_state_cached_per_sha():
    client = FakeClient([{"status": "completed", "conclusion": "success"}])
    ci.ci_state("abc123", client=client)
    ci.ci_state("abc123", client=client)
    assert client.calls == 1  # second call served from the cache
    # a different SHA is not cached against the first
    ci.ci_state("def456", client=client)
    assert client.calls == 2


async def test_ci_status_endpoint(client, monkeypatch):
    """The /ci-status endpoint returns the state of the running commit (dev: unknown)."""
    monkeypatch.setenv("COMMIT_SHA", "unknown")
    resp = await client.get("/api/v1/ci-status")
    assert resp.status_code == 200
    assert resp.json() == {"state": "unknown", "sha": "unknown"}


async def test_ci_status_endpoint_uses_build_sha(client, monkeypatch):
    """A real build SHA is forwarded to ci_state (green here, no HTTP: cached green)."""
    monkeypatch.setenv("COMMIT_SHA", "realsha123")
    ci._clear_cache()
    # seed the cache so no network call is made
    ci._CACHE.set("realsha123", {"state": "green", "sha": "realsha123"})
    resp = await client.get("/api/v1/ci-status")
    assert resp.status_code == 200
    assert resp.json() == {"state": "green", "sha": "realsha123"}
