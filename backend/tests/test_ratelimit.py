"""Anti brute-force rate limiting tests (F7): login/register/invite per IP.

The limiter is in-memory and global: conftest.py resets it before each test
(autouse fixture). With multiple workers the limit is per worker — in prod,
nginx limit_req is the robust mitigation (documented in app/api/ratelimit.py).
"""


async def test_login_rate_limit_11th_request_429(client, researcher_user, do_login):
    """11th consecutive login same IP -> 429 rate_limited (limit 10/15min)."""
    for i in range(10):
        resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
        assert resp.status_code == 200, f"login {i + 1} devrait passer"
    resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_register_rate_limit_6th_request_429(client, admin_user, mock_mailer, do_login):
    """6th register same IP -> 429 rate_limited (limit 5/15min)."""
    from tests.test_auth import _invite, _token_from_email

    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    # 1st register -> 201; 2nd-5th -> 409 (user exists) but count toward the limit.
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 201
    for i in range(2, 6):
        resp = await client.post(
            "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
        )
        assert resp.status_code == 409, f"register {i} should be 409"
    # 6e → 429 (rate limit)
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_invite_rate_limit_21st_request_429(client, admin_user, mock_mailer, do_login):
    """21st invite same IP -> 429 rate_limited (limit 20/15min)."""
    await do_login("admin@lab.fr", "admin-pass-123")
    for i in range(20):
        resp = await client.post("/api/v1/auth/invite", json={"email": f"user{i}@lab.fr"})
        assert resp.status_code == 202, f"invite {i + 1} devrait passer"
    resp = await client.post("/api/v1/auth/invite", json={"email": "user20@lab.fr"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_ratelimit_respects_x_forwarded_for(client, researcher_user):
    """X-Forwarded-For respected: 2 different IPs -> 2 independent counters."""
    payload = {"email": "researcher@lab.fr", "password": "researcher-pass-123", "consent": True}
    # 10 logins depuis l'IP 1.2.3.4 → OK.
    for i in range(10):
        resp = await client.post(
            "/api/v1/auth/login", json=payload, headers={"X-Forwarded-For": "1.2.3.4"}
        )
        assert resp.status_code == 200, f"login {i + 1} (IP 1.2.3.4) devrait passer"
    # 11e depuis 1.2.3.4 → 429.
    resp = await client.post(
        "/api/v1/auth/login", json=payload, headers={"X-Forwarded-For": "1.2.3.4"}
    )
    assert resp.status_code == 429
    # But from another IP (5.6.7.8) -> OK (independent counter).
    resp = await client.post(
        "/api/v1/auth/login", json=payload, headers={"X-Forwarded-For": "5.6.7.8"}
    )
    assert resp.status_code == 200
