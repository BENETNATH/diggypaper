"""Scrub secrets (userinfo, query, fragment) from OPENAI_API identifiers.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29 15:00:00.000000

Defense in depth: output masking (payload) is not enough, the secret must
stop existing in clear at rest (dumps, backups). A ``<url>#<model>`` row
becomes the sanitized URL (scheme://host[:port] + path, no userinfo/query/fragment)
+ the model after the LAST "#". The other backends are untouched. The downgrade
is a no-op: a secret is never restored.
"""

import logging
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger(__name__)


def _scrub(ident: str) -> str:
    """URL = before the FIRST "#" (sanitized), model = after the LAST "#" (fragment discarded)."""
    base, model = ident.split("#", 1)[0], ident.rsplit("#", 1)[1] if "#" in ident else ""
    p = urlsplit(base)
    netloc = p.netloc.rsplit("@", 1)[-1]  # userinfo retiré (hôte, [IPv6], port tels quels)
    clean = urlunsplit((p.scheme, netloc, p.path, "", ""))  # query + fragment vidés
    return clean + (f"#{model}" if model else "")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, name, model_identifier FROM model_registry "
            "WHERE backend_type = 'OPENAI_API'"
        )
    ).all()
    cleaned: list[str] = []
    for row_id, name, ident in rows:
        new = _scrub(ident)
        if new != ident:
            conn.execute(
                sa.text("UPDATE model_registry SET model_identifier = :m WHERE id = :i"),
                {"m": new, "i": row_id},
            )
            cleaned.append(name)
    # Only the NAMES, never the URL: it is what carries the secret.
    log.info("0031: %d identifiant(s) OPENAI_API nettoyé(s): %s", len(cleaned), ", ".join(cleaned))


def downgrade() -> None:
    """No-op: a secret is never put back in clear."""
