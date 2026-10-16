"""articles.origin / origin_seeds / origin_ref (chaînage de citations)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-17 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("origin", sa.String(), nullable=True))
    op.add_column("articles", sa.Column("origin_seeds", sa.Integer(), nullable=True))
    op.add_column("articles", sa.Column("origin_ref", sa.String(), nullable=True))
    op.create_index("ix_articles_origin_ref", "articles", ["origin_ref"])


def downgrade() -> None:
    op.drop_index("ix_articles_origin_ref", table_name="articles")
    op.drop_column("articles", "origin_ref")
    op.drop_column("articles", "origin_seeds")
    op.drop_column("articles", "origin")
