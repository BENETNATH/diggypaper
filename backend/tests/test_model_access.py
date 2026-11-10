"""Smoke tests for the pinned transport (model_access.PinnedTransport).

The transport re-implements httpx internals through private APIs
(``httpx._transports.default``, ``httpcore.AsyncConnectionPool``) to pin the
TCP target to a pre-validated IP (SSRF / DNS-rebinding hardening). These tests
pin the fragile surface: they fail on an httpx/httpcore upgrade that breaks the
wiring, before any real request can (caveman-review 2026-11-10: no version pin,
no test).
"""

import httpcore
import httpx
import pytest

from app.model_access import PinnedTransport, _PinnedBackend, pinned_client


class _RecordingBackend:
    """Stands in for the default backend: records what connect_tcp targets."""

    def __init__(self):
        self.tcp = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.tcp.append((host, port))
        return "socket"

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("not used by the pinned transport")

    async def sleep(self, seconds):
        pass


async def test_pinned_backend_connects_to_the_validated_ip():
    """connect_tcp must target the pinned IP, whatever the requested host."""
    inner = _RecordingBackend()
    backend = _PinnedBackend("203.0.113.7", inner=inner)  # type: ignore[arg-type]
    await backend.connect_tcp("api.example.com", 443)
    assert inner.tcp == [("203.0.113.7", 443)]


async def test_pinned_transport_keeps_the_hostname_for_sni():
    """The httpcore request keeps the hostname (Host header / TLS SNI) — only
    the TCP target is replaced by the backend."""
    transport = PinnedTransport("203.0.113.7")

    class _RecordingPool:
        def __init__(self):
            self.requests = []

        async def handle_async_request(self, req):
            self.requests.append(req)
            raise httpcore.ConnectError("stop before the network")

        async def aclose(self):
            pass

    pool = _RecordingPool()
    transport._pool = pool
    request = httpx.Request("GET", "https://api.example.com/v1/chat")
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(request)
    assert pool.requests[0].url.host == b"api.example.com"
    assert pool.requests[0].url.target == b"/v1/chat"


async def test_pinned_client_builds_and_closes():
    """pinned_client(timeout, ip) builds a working AsyncClient (no network)."""
    client = pinned_client(5.0, "203.0.113.7")
    try:
        assert client._transport is not None
    finally:
        await client.aclose()
