"""Model access (ownership / sharing), deactivatable accounts, deletion of a user
with their leftovers, tracked invitations (list, resend, revocation)."""

import pytest
from sqlalchemy import func, select

from app.core.security import decode_invite_token
from app.db.models import (
    Article,
    ExportJob,
    HumanAnnotation,
    Invitation,
    ModelRegistry,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from tests.conftest import _make_user

API = "/api/v1"


@pytest.fixture
def public_urls(monkeypatch):
    """No DNS in tests: every URL is "public"."""
    monkeypatch.setattr("app.api.v1.models.check_public_url", lambda url: None)


async def _login(client, email, password):
    client.cookies.clear()
    r = await client.post(
        f"{API}/auth/login", json={"email": email, "password": password, "consent": True}
    )
    assert r.status_code == 200, r.text


@pytest.fixture
async def alice(session):
    return await _make_user(session, "alice@lab.fr", UserRole.RESEARCHER, "alice-pass-123", True)


@pytest.fixture
async def bob(session):
    return await _make_user(session, "bob@lab.fr", UserRole.RESEARCHER, "bob-pass-12345", True)


async def _project(session, lead: User, title="P") -> Project:
    p = Project(title=title, created_by=lead.id)
    session.add(p)
    await session.commit()
    session.add(
        ProjectMember(project_id=p.id, user_id=lead.id, role_in_project=ProjectMemberRole.LEAD)
    )
    await session.commit()
    return p


async def _names(client, **params):
    return sorted(
        m["name"] for m in (await client.get(f"{API}/models", params=params)).json()["items"]
    )


EXT = {"name": "My-GPT", "base_url": "https://api.example.com/v1", "model": "m", "api_key": "k"}


# --- models: ownership and sharing ----------------------------------------------------


async def test_user_defines_and_shares_own_external_model(client, session, alice, bob, public_urls):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    r = await client.post(f"{API}/models/external", json=EXT)
    assert r.status_code == 201 and r.json()["mine"] is True
    mid = r.json()["id"]
    assert "api_key" not in r.text and "k" != r.json().get("model_identifier")
    assert await _names(client) == ["My-GPT"]

    await _login(client, "bob@lab.fr", "bob-pass-12345")
    assert await _names(client) == []  # pas à lui
    assert (await client.get(f"{API}/models/{mid}/shares")).status_code == 404
    assert (await client.delete(f"{API}/models/{mid}")).status_code == 404 or 403

    await _login(client, "alice@lab.fr", "alice-pass-123")
    s = await client.post(f"{API}/models/{mid}/shares", json={"email": "BOB@lab.fr"})
    assert s.status_code == 201
    assert (
        await client.post(f"{API}/models/{mid}/shares", json={"email": "bob@lab.fr"})
    ).status_code == 409
    assert (
        await client.post(f"{API}/models/{mid}/shares", json={"email": "no@x.fr"})
    ).status_code == 404
    shares = (await client.get(f"{API}/models/{mid}/shares")).json()["items"]
    assert [x["email"] for x in shares] == ["bob@lab.fr"]

    await _login(client, "bob@lab.fr", "bob-pass-12345")
    assert await _names(client) == ["My-GPT"]
    # bob can use it but neither share it further nor delete it
    assert (
        await client.post(f"{API}/models/{mid}/shares", json={"email": "alice@lab.fr"})
    ).status_code == 404
    assert (await client.delete(f"{API}/models/{mid}")).status_code == 403

    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert (await client.delete(f"{API}/models/{mid}/shares/{shares[0]['id']}")).status_code == 204
    await _login(client, "bob@lab.fr", "bob-pass-12345")
    assert await _names(client) == []


async def test_model_shared_with_project_follows_membership(
    client, session, alice, bob, public_urls
):
    project = await _project(session, alice)
    other = await _project(session, alice, "Other")
    await _login(client, "alice@lab.fr", "alice-pass-123")
    mid = (await client.post(f"{API}/models/external", json=EXT)).json()["id"]
    r = await client.post(f"{API}/models/{mid}/shares", json={"project_id": str(project.id)})
    assert r.status_code == 201
    # a project the user is not a member of: not found
    stranger = await _project(session, bob, "Bobs")
    r = await client.post(f"{API}/models/{mid}/shares", json={"project_id": str(stranger.id)})
    assert r.status_code == 404

    session.add(
        ProjectMember(project_id=project.id, user_id=bob.id, role_in_project=ProjectMemberRole.LEAD)
    )
    await session.commit()
    await _login(client, "bob@lab.fr", "bob-pass-12345")
    assert await _names(client, project_id=str(project.id)) == ["My-GPT"]
    assert await _names(client, project_id=str(other.id)) == []  # partagé à un autre projet
    # removed from the project: no more access
    await session.execute(
        ProjectMember.__table__.delete().where(
            ProjectMember.user_id == bob.id, ProjectMember.project_id == project.id
        )
    )
    await session.commit()
    assert await _names(client) == []


async def test_platform_model_needs_a_share_and_inference_checks_it(
    client, session, admin_user, alice
):
    from app.db.models import ProjectStatus, PromptTemplate

    project = await _project(session, alice)
    project.status = ProjectStatus.FROZEN
    model = ModelRegistry(name="Kev", model_identifier="kev:q4")
    session.add_all(
        [model, PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")]
    )
    await session.commit()
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert await _names(client) == []
    r = await client.post(f"{API}/projects/{project.id}/inference", json={"model": "Kev"})
    assert r.status_code == 422 and r.json()["detail"] == "unknown model"
    r = await client.post(f"{API}/projects/{project.id}/benchmarks", json={"models": ["Kev"]})
    assert r.status_code == 422

    await _login(client, "admin@lab.fr", "admin-pass-123")  # l'admin partage au projet
    r = await client.post(f"{API}/models/{model.id}/shares", json={"project_id": str(project.id)})
    assert r.status_code == 201
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert await _names(client, project_id=str(project.id)) == ["Kev"]


async def test_non_admin_cannot_point_model_at_internal_network(client, session, alice, admin_user):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    for url in (
        "http://127.0.0.1:11434/v1",
        "http://10.0.0.5/v1",
        "http://[::1]/v1",
        "ftp://x.y/v1",
    ):
        r = await client.post(f"{API}/models/external", json={**EXT, "base_url": url})
        assert r.status_code == 422, url
        t = await client.post(f"{API}/models/external/test", json={**EXT, "base_url": url})
        assert t.status_code == 422, url
    await _login(client, "admin@lab.fr", "admin-pass-123")  # l'admin n'est pas restreint
    r = await client.post(
        f"{API}/models/external", json={**EXT, "base_url": "http://ollama:11434/v1"}
    )
    assert r.status_code == 201 and r.json()["platform"] is True


# --- accounts: deactivation, deletion ----------------------------------------------


async def test_disabled_account_loses_access_and_cannot_login(client, session, admin_user, alice):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert (await client.get(f"{API}/auth/me")).status_code == 200
    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.patch(f"{API}/admin/users/{alice.id}", json={"is_active": False})
    assert r.status_code == 200
    r = await client.post(
        f"{API}/auth/login",
        json={"email": "alice@lab.fr", "password": "alice-pass-123", "consent": True},
    )
    assert r.status_code == 403
    # an already-open session is cut off
    client.cookies.clear()
    await _login(client, "admin@lab.fr", "admin-pass-123")
    await client.patch(f"{API}/admin/users/{alice.id}", json={"is_active": True})
    await _login(client, "alice@lab.fr", "alice-pass-123")
    (await session.get(User, alice.id)).is_active = False  # désactivé pendant la session
    await session.commit()
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_admin_endpoints_are_admin_only_and_protect_last_admin(
    client, session, admin_user, alice
):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    for path in ("users", "invitations", "projects"):
        assert (await client.get(f"{API}/admin/{path}")).status_code == 403
    assert (await client.delete(f"{API}/admin/users/{admin_user.id}")).status_code == 403
    await _login(client, "admin@lab.fr", "admin-pass-123")
    assert (
        await client.patch(f"{API}/admin/users/{admin_user.id}", json={"is_active": False})
    ).status_code == 409
    assert (
        await client.patch(f"{API}/admin/users/{admin_user.id}", json={"role": "RESEARCHER"})
    ).status_code == 409
    assert (await client.delete(f"{API}/admin/users/{admin_user.id}")).status_code == 409
    r = await client.patch(
        f"{API}/admin/users/{alice.id}",
        json={"role": "ADMIN", "full_name": "Alice A", "language_preference": "de"},
    )
    assert r.status_code == 200
    users = {u["email"]: u for u in (await client.get(f"{API}/admin/users")).json()["items"]}
    assert (
        users["alice@lab.fr"]["role"] == "ADMIN"
        and users["alice@lab.fr"]["language_preference"] == "de"
    )


async def test_delete_user_with_their_projects_and_leftovers(
    client, session, admin_user, alice, bob, public_urls, tmp_path, monkeypatch
):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    mine = await _project(session, alice, "Alice project")
    shared = await _project(session, bob, "Bob project")
    session.add(
        ProjectMember(
            project_id=shared.id, user_id=alice.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    art = Article(project_id=shared.id, pmid="1")
    session.add(art)
    await session.commit()
    session.add(HumanAnnotation(article_id=art.id, user_id=alice.id, decision_json={"a": 1}))
    (tmp_path / "crate.zip").write_bytes(b"zip")
    session.add(
        ExportJob(project_id=mine.id, user_id=alice.id, status="COMPLETED", filename="crate.zip")
    )
    await session.commit()
    await _login(client, "alice@lab.fr", "alice-pass-123")
    await client.post(f"{API}/models/external", json=EXT)
    mine_id, shared_id, alice_id = mine.id, shared.id, alice.id

    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.delete(
        f"{API}/admin/users/{alice.id}", params={"delete_their_projects": "true"}
    )
    assert r.status_code == 200 and r.json()["deleted_projects"] == 1
    session.expire_all()
    assert await session.get(User, alice_id) is None
    assert await session.get(Project, mine_id) is None
    assert not (tmp_path / "crate.zip").exists()  # le zip sur disque part aussi
    assert await session.get(Project, shared_id) is not None  # projet de bob intact
    assert (
        await session.execute(select(func.count()).select_from(ModelRegistry))
    ).scalar_one() == 0
    ann = (await session.execute(select(HumanAnnotation))).scalars().one()
    assert ann.user_id is None and ann.annotator_hash  # annotation conservée, anonymisée


async def test_delete_user_keeps_projects_and_reports_them(client, session, admin_user, alice):
    await _project(session, alice, "Alice project")
    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.delete(f"{API}/admin/users/{alice.id}")
    assert r.status_code == 200
    assert r.json() == {"deleted_projects": 0, "orphaned_projects": ["Alice project"]}
    projects = (await client.get(f"{API}/admin/projects")).json()["items"]
    assert [p["title"] for p in projects] == ["Alice project"] and projects[0]["created_by"] is None


# --- invitations suivies ---------------------------------------------------------------


async def test_pending_invitations_listed_resent_and_revoked(client, session, admin_user, alice):
    project = await _project(session, alice)
    await _login(client, "alice@lab.fr", "alice-pass-123")
    r = await client.post(
        f"{API}/projects/{project.id}/members/invite",
        json={"email": "new@lab.fr", "role": "ANNOTATOR", "language": "de"},
    )
    assert r.status_code == 201
    link = r.json()["link"]  # pas de SMTP en test : le lien est renvoyé
    token = link.split("token=")[1]
    assert decode_invite_token(token)["iid"]

    await _login(client, "admin@lab.fr", "admin-pass-123")
    items = (await client.get(f"{API}/admin/invitations")).json()["items"]
    assert [(i["email"], i["language"], i["project"]) for i in items] == [("new@lab.fr", "de", "P")]
    # re-inviting the same address does not duplicate
    await _login(client, "alice@lab.fr", "alice-pass-123")
    await client.post(
        f"{API}/projects/{project.id}/members/invite",
        json={"email": "new@lab.fr", "language": "es"},
    )
    await _login(client, "admin@lab.fr", "admin-pass-123")
    items = (await client.get(f"{API}/admin/invitations")).json()["items"]
    assert len(items) == 1 and items[0]["language"] == "es"

    r = await client.post(f"{API}/admin/invitations/{items[0]['id']}/resend")
    assert r.status_code == 200 and r.json()["link"]  # sans SMTP : lien à transmettre
    assert (await client.delete(f"{API}/admin/invitations/{items[0]['id']}")).status_code == 204
    assert (await client.get(f"{API}/admin/invitations")).json()["items"] == []
    client.cookies.clear()
    r = await client.post(
        f"{API}/auth/register", json={"token": token, "password": "new-pass-1234"}
    )
    assert r.status_code == 401  # lien révoqué


async def test_register_marks_invitation_accepted(
    client, session, admin_user, mock_mailer, monkeypatch
):
    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.post(f"{API}/auth/invite", json={"email": "x@lab.fr", "language": "es"})
    assert r.status_code == 202
    token = mock_mailer.sent[-1]["body"].split("token=")[1].split()[0]
    assert (await client.get(f"{API}/admin/invitations")).json()["items"][0]["language"] == "es"
    client.cookies.clear()
    r = await client.post(
        f"{API}/auth/register", json={"token": token, "password": "new-pass-1234"}
    )
    assert r.status_code == 201 and r.json()["language_preference"] == "es"
    inv = (await session.execute(select(Invitation))).scalars().one()
    assert inv.accepted_at is not None
    await _login(client, "admin@lab.fr", "admin-pass-123")
    assert (await client.get(f"{API}/admin/invitations")).json()["items"] == []


# --- creators and invitees ---------------------------------------------------------------


async def _exists(session, email):
    session.expire_all()
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


@pytest.fixture
async def guest(session):
    return await _make_user(
        session, "guest@lab.fr", UserRole.RESEARCHER, "guest-pass-123", True, creator=False
    )


async def test_guest_cannot_create_projects_creator_can(client, session, alice, guest):
    await _login(client, "guest@lab.fr", "guest-pass-123")
    assert (await client.post(f"{API}/projects", json={"title": "X"})).status_code == 403
    assert (await client.get(f"{API}/auth/me")).json()["can_create_projects"] is False
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert (await client.post(f"{API}/projects", json={"title": "X"})).status_code == 201
    assert (await client.get(f"{API}/auth/me")).json()["can_create_projects"] is True


async def test_guest_removed_from_last_project_is_deleted_creator_is_not(
    client, session, admin_user, alice, bob, guest
):
    p = await _project(session, alice)
    for u in (bob, guest):  # bob = créateur, guest = invité
        session.add(
            ProjectMember(
                project_id=p.id, user_id=u.id, role_in_project=ProjectMemberRole.ANNOTATOR
            )
        )
    await session.commit()
    pid, bid, gid = p.id, bob.id, guest.id
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert (await client.delete(f"{API}/projects/{pid}/members/{gid}")).status_code == 204
    assert await _exists(session, "guest@lab.fr") is None
    assert (await client.delete(f"{API}/projects/{pid}/members/{bid}")).status_code == 204
    assert await _exists(session, "bob@lab.fr") is not None  # créateur : conservé
    # the creator deleting their project keeps their account (regression B1)
    assert (await client.delete(f"{API}/projects/{pid}")).status_code == 204
    assert await _exists(session, "alice@lab.fr") is not None


async def test_deleting_project_deletes_guest_members_without_other_project(
    client, session, admin_user, alice, guest
):
    p = await _project(session, alice)
    session.add(
        ProjectMember(
            project_id=p.id, user_id=guest.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    await session.commit()
    pid = p.id
    await _login(client, "alice@lab.fr", "alice-pass-123")
    await client.delete(f"{API}/projects/{pid}")
    assert await _exists(session, "guest@lab.fr") is None
    assert await _exists(session, "alice@lab.fr") is not None


async def test_admin_purge_orphans_only_guests(client, session, admin_user, alice, bob, guest):
    await _project(session, alice)
    await _login(client, "alice@lab.fr", "alice-pass-123")
    assert (await client.post(f"{API}/admin/users/purge-orphans")).status_code == 403
    await _login(client, "admin@lab.fr", "admin-pass-123")
    assert (await client.post(f"{API}/admin/users/purge-orphans")).json() == {"deleted": 1}
    assert await _exists(session, "guest@lab.fr") is None
    assert await _exists(session, "bob@lab.fr") is not None  # créateur sans projet : conservé


async def test_admin_invites_creator_and_toggles_flag(client, session, admin_user, guest):
    await _login(client, "admin@lab.fr", "admin-pass-123")
    r = await client.post(f"{API}/auth/invite", json={"email": "new@lab.fr"})
    assert r.status_code == 202
    inv = (
        await session.execute(select(Invitation).where(Invitation.email == "new@lab.fr"))
    ).scalar_one()
    assert inv.can_create_projects is True
    gid = guest.id
    r = await client.patch(f"{API}/admin/users/{gid}", json={"can_create_projects": True})
    assert r.status_code == 200
    users = (await client.get(f"{API}/admin/users")).json()["items"]
    assert next(u for u in users if u["id"] == str(gid))["can_create_projects"] is True


# --- durcissement ------------------------------------------------------------------------


def test_spreadsheet_formulas_are_neutralized():
    from app.api.v1.export import _cell
    from app.crate import neutralize

    for evil in ('=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"):
        assert neutralize(evil) == "'" + evil
        assert _cell(evil).startswith("'")
    assert _cell(["=cmd|'/C calc'!A1", "b"]).startswith("'=")  # liste aplatie
    assert neutralize("normal") == "normal" and neutralize(3.5) == 3.5 and neutralize(None) is None


async def test_user_model_url_rechecked_at_call_time(monkeypatch):
    from app.inference import InferenceError, OpenAICompatibleService

    def rebound(url):
        raise ValueError("now internal")

    monkeypatch.setattr("app.model_access.check_public_url", rebound)
    with pytest.raises(InferenceError, match="url_not_allowed"):
        await OpenAICompatibleService("http://x.example/v1", public_only=True).infer("m", "s", "u")


async def test_team_invite_and_external_test_are_rate_limited(
    client, session, alice, public_urls, monkeypatch
):
    from app.inference import InferenceError, OpenAICompatibleService

    async def boom(self, *a):
        raise InferenceError("x")

    monkeypatch.setattr(OpenAICompatibleService, "infer", boom)
    p = await _project(session, alice)
    await _login(client, "alice@lab.fr", "alice-pass-123")
    codes = [
        (
            await client.post(
                f"{API}/projects/{p.id}/members/invite", json={"email": f"n{i}@lab.fr"}
            )
        ).status_code
        for i in range(21)
    ]
    assert codes[:20] == [201] * 20 and codes[20] == 429
    codes = [
        (await client.post(f"{API}/models/external/test", json=EXT)).status_code for _ in range(11)
    ]
    assert codes[:10] == [200] * 10 and codes[10] == 429


async def test_jwt_revoked_on_password_change_and_logout(client, session, alice):
    await _login(client, "alice@lab.fr", "alice-pass-123")
    old = client.cookies.get("access_token")
    assert (await client.get(f"{API}/auth/me")).status_code == 200
    r = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": "alice-pass-123", "new_password": "alice-new-pass-1"},
    )
    assert r.status_code == 204
    client.cookies.set("access_token", old)
    assert (await client.get(f"{API}/auth/me")).status_code == 401  # ancien JWT révoqué
    await _login(client, "ALICE@lab.fr", "alice-new-pass-1")  # login insensible à la casse
    assert (await client.get(f"{API}/auth/me")).status_code == 200
    fresh = client.cookies.get("access_token")
    assert (await client.post(f"{API}/auth/logout")).status_code == 204
    client.cookies.set("access_token", fresh)
    assert (await client.get(f"{API}/auth/me")).status_code == 401  # logout révoque aussi


async def test_import_and_pdf_upload_need_lead(client, session, alice, bob):
    p = await _project(session, alice)
    session.add(
        ProjectMember(project_id=p.id, user_id=bob.id, role_in_project=ProjectMemberRole.ANNOTATOR)
    )
    await session.commit()
    await _login(client, "bob@lab.fr", "bob-pass-12345")
    assert (
        await client.post(f"{API}/projects/{p.id}/import", json={"query": "x"})
    ).status_code == 403
    assert (await client.post(f"{API}/projects/{p.id}/export/crate")).status_code == 403
