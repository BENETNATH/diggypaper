"""Tests des routes Prompt Studio (app/api/v1/prompts.py) — 100 % offline."""

from app.db.models import Project, ProjectMember, ProjectMemberRole


async def _make_project_with_lead(session, lead_user, other_user=None):
    """Projet dont `lead_user` est LEAD ; `other_user` (optionnel) est ANNOTATOR."""
    project = Project(title="P", created_by=lead_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=lead_user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    if other_user is not None:
        session.add(
            ProjectMember(
                project_id=project.id,
                user_id=other_user.id,
                role_in_project=ProjectMemberRole.ANNOTATOR,
            )
        )
    await session.commit()
    return project


async def test_create_prompt_lead(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = await _make_project_with_lead(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/prompts",
        json={
            "version_number": "v1.0",
            "title": "Extraction",
            "system_prompt": "You are a scientific evaluator.",
            "user_prompt_schema": "Article Text:\n{methods_text}",
            "prompt_type": "EXTRACTION",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version_number"] == "v1.0"
    assert body["status"] == "DRAFT"


async def test_list_and_get_prompt(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = await _make_project_with_lead(session, researcher_user)
    pid = (
        await client.post(
            f"/api/v1/projects/{project.id}/prompts",
            json={"version_number": "v1.0", "system_prompt": "SYS"},
        )
    ).json()["id"]
    resp = await client.get(f"/api/v1/projects/{project.id}/prompts")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    resp = await client.get(f"/api/v1/projects/{project.id}/prompts/{pid}")
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "SYS"


async def test_create_prompt_empty_system_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = await _make_project_with_lead(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/prompts",
        json={"version_number": "v1.0", "system_prompt": ""},
    )
    assert resp.status_code == 422


async def test_update_prompt_status_production(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project = await _make_project_with_lead(session, researcher_user)
    pid = (
        await client.post(
            f"/api/v1/projects/{project.id}/prompts",
            json={"version_number": "v1.0", "system_prompt": "SYS"},
        )
    ).json()["id"]
    resp = await client.patch(
        f"/api/v1/projects/{project.id}/prompts/{pid}", json={"status": "PRODUCTION"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "PRODUCTION"


async def test_annotator_cannot_create_prompt_403(client, session, researcher_user, do_login):
    """Un ANNOTATOR (non-LEAD) ne peut pas créer de prompt."""
    from app.core.security import hash_password
    from app.db.models import User, UserRole

    annotator = User(
        email="annotator@lab.fr",
        hashed_password=hash_password("annotator-pass-123"),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
    )
    session.add(annotator)
    await session.commit()
    lead = researcher_user
    project = await _make_project_with_lead(session, lead, other_user=annotator)
    await do_login("annotator@lab.fr", "annotator-pass-123")
    resp = await client.post(
        f"/api/v1/projects/{project.id}/prompts",
        json={"version_number": "v1.0", "system_prompt": "SYS"},
    )
    assert resp.status_code == 403


async def test_non_member_cannot_list_prompts_403(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    # Projet sans membership pour le researcher.
    project = Project(title="Foreign", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/prompts")
    assert resp.status_code == 403
