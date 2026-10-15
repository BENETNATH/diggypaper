"""articles.sections + projects.text_sections

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("sections", postgresql.JSONB(), nullable=True))
    op.add_column("projects", sa.Column("text_sections", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "text_sections")
    op.drop_column("articles", "sections")
