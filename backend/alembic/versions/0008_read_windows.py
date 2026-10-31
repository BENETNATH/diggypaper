"""projects.read_windows: text length read per article (decision models, CPU)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("read_windows", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "read_windows")
