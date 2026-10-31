"""Removes PMC "full texts" too short (non-archived preprint notices)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-15 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIN_CHARS = 1500  # app.fulltext.MIN_CHARS (figé : une migration ne dépend pas du code courant)


def upgrade() -> None:
    bind = op.get_bind()
    articles = sa.table(
        "articles",
        sa.column("id", sa.Uuid()),
        sa.column("sections", sa.JSON()),
        sa.column("fulltext_source", sa.String()),
        sa.column("fulltext_url", sa.String()),
    )
    rows = bind.execute(
        sa.select(articles.c.id, articles.c.sections).where(articles.c.fulltext_source == "pmc")
    ).all()
    short = [
        r.id for r in rows if sum(len(s.get("text", "")) for s in (r.sections or [])) < MIN_CHARS
    ]
    for i in range(0, len(short), 500):
        bind.execute(
            sa.update(articles)
            .where(articles.c.id.in_(short[i : i + 500]))
            .values(sections=[], fulltext_source=None, fulltext_url=None)
        )


def downgrade() -> None:
    pass  # données de poubelle : rien à restaurer
