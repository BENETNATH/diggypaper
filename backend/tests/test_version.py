"""The public /version endpoint: footer + crate provenance (single source = VERSION file)."""


async def test_version_endpoint_returns_semver(client):
    """The /version endpoint returns the app version (semver from the VERSION file)."""
    resp = await client.get("/api/v1/version")
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.0.0"
