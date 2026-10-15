"""Tests de rate limiting anti brute-force (F7) : login/register/invite par IP.

Le limiteur est in-memory et global : conftest.py le réinitialise avant chaque test
(fixture autouse). En multi-workers la limite est par worker — en prod, nginx
limit_req est la mitigation robuste (documenté dans app/api/ratelimit.py).
"""


async def test_login_rate_limit_11th_request_429(client, researcher_user, do_login):
    """11e login consécutif même IP → 429 rate_limited (limite 10/15min)."""
    for i in range(10):
        resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
        assert resp.status_code == 200, f"login {i + 1} devrait passer"
    resp = await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_register_rate_limit_6th_request_429(client, admin_user, mock_mailer, do_login):
    """6e register même IP → 429 rate_limited (limite 5/15min)."""
    from tests.test_auth import _invite, _token_from_email

    await _invite(client, do_login, "newbie@lab.fr")
    token = _token_from_email(mock_mailer)
    # 1er register → 201 ; 2e-5e → 409 (user existe) mais comptent dans la limite.
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 201
    for i in range(2, 6):
        resp = await client.post(
            "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
        )
        assert resp.status_code == 409, f"register {i} devrait être 409"
    # 6e → 429 (rate limit)
    resp = await client.post(
        "/api/v1/auth/register", json={"token": token, "password": "new-pass-123"}
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_invite_rate_limit_21st_request_429(client, admin_user, mock_mailer, do_login):
    """21e invite même IP → 429 rate_limited (limite 20/15min)."""
    await do_login("admin@lab.fr", "admin-pass-123")
    for i in range(20):
        resp = await client.post("/api/v1/auth/invite", json={"email": f"user{i}@lab.fr"})
        assert resp.status_code == 202, f"invite {i + 1} devrait passer"
    resp = await client.post("/api/v1/auth/invite", json={"email": "user20@lab.fr"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"


async def test_ratelimit_respects_x_forwarded_for(client, researcher_user):
    """X-Forwarded-For respecté : 2 IPs différentes → 2 compteurs indépendants."""
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
    # Mais depuis une autre IP (5.6.7.8) → OK (compteur indépendant).
    resp = await client.post(
        "/api/v1/auth/login", json=payload, headers={"X-Forwarded-For": "5.6.7.8"}
    )
    assert resp.status_code == 200
