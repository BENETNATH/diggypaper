"""Phase 3 multi-sources : l'identifiant natif (``native_id``) dans la recherche rapide
(spec 2026-10-29-sources-phase3 D5) — 100 % offline."""

import uuid
from pathlib import Path

from app.db.models import Article

BACKEND = Path(__file__).parent.parent


async def test_search_blob_includes_native_id(client, session, researcher_user, do_login):
    """Un article EPMC sans PMID/DOI est trouvable par son ``native_id`` (``W123``)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    resp = await client.post("/api/v1/projects", json={"title": "Native search"})
    print("DBG", resp.status_code, resp.text)
    pid = resp.json()["id"]
    arts = [Article(project_id=uuid.UUID(pid), pmid=str(i), title=f"A{i}") for i in range(1, 5)]
    session.add_all(arts)
    arts[0].source, arts[0].native_id, arts[0].pmid = "europepmc", "W123", None
    arts[3].pmid = "5550123"
    await session.commit()
    base = f"/api/v1/projects/{pid}"
    r = await client.get(f"{base}/articles", params={"q": "W123"})
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()["items"]] == ["A1"]
    # garde : le PMID reste trouvable
    r = await client.get(f"{base}/articles", params={"q": "5550123"})
    assert [i["title"] for i in r.json()["items"]] == ["A4"]


def test_migration_0032_recreates_trgm_index():
    """0032 (down 0031) est la tête ; l'expression recreate = 0028 + ``native_id`` après
    ``pmid`` (sinon l'index ne sert plus) ; PG-only (no-op SQLite)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.test_source_identity import _migration

    mod = _migration("0032")
    assert mod.revision == "0032" and mod.down_revision == "0031"
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert script.get_heads() == ["0032"]
    src = Path(mod.__file__).read_text()
    assert "coalesce(pmid, '') || ' ' || coalesce(native_id, '') || ' ' || coalesce(doi, '')" in src
    assert "postgresql" in src  # no-op hors PG, comme 0028
