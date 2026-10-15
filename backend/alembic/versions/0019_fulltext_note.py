"""Note « pas de texte intégral » (préprint non archivable dans PMC…)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-15 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("fulltext_note", sa.String()))


def downgrade() -> None:
    op.drop_column("articles", "fulltext_note")
