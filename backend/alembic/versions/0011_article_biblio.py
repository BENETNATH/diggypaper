"""articles: journal, publication date, authors, publication types

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("journal", sa.String(), nullable=True))
    op.add_column("articles", sa.Column("pub_date", sa.String(), nullable=True))
    op.add_column("articles", sa.Column("pub_year", sa.Integer(), nullable=True))
    op.add_column("articles", sa.Column("authors", postgresql.JSONB(), nullable=True))
    op.add_column("articles", sa.Column("pub_types", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for c in ("pub_types", "authors", "pub_year", "pub_date", "journal"):
        op.drop_column("articles", c)
