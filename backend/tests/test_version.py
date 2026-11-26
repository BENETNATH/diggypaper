"""The public /version endpoint: footer + crate provenance (single source = VERSION file)."""

import re
from pathlib import Path


async def test_version_endpoint_returns_semver(client):
    """The /version endpoint returns the app version (semver from the VERSION file)."""
    expected = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    resp = await client.get("/api/v1/version")
    assert resp.status_code == 200
    assert resp.json()["version"] == expected
    assert re.fullmatch(r"\d+\.\d+\.\d+", expected)
