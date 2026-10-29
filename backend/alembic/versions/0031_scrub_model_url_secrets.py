"""Nettoie les secrets (userinfo, query, fragment) des identifiants OPENAI_API.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29 15:00:00.000000

Défense en profondeur : le masquage en sortie (payload) ne suffit pas, le secret doit
cesser d'exister en clair au repos (dumps, sauvegardes). Une ligne ``<url>#<modèle>``
devient l'URL assainie (scheme://hôte[:port] + chemin, sans userinfo/query/fragment)
+ le modèle après le DERNIER « # ». Les autres backends sont intacts. Le downgrade est
un no-op : on ne restaure jamais un secret.
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
    """URL = avant le PREMIER « # » (assainie), modèle = après le DERNIER « # » (fragment jeté)."""
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
    # Les NOMS seulement, jamais l'URL : c'est elle qui porte le secret.
    log.info("0031: %d identifiant(s) OPENAI_API nettoyé(s): %s", len(cleaned), ", ".join(cleaned))


def downgrade() -> None:
    """No-op : on ne remet jamais un secret en clair."""
