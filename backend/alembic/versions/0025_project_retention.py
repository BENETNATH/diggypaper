"""projects.kept_at / retention_warned_at (suppression des projets inactifs)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-17 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "projects", sa.Column("retention_warned_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "retention_warned_at")
    op.drop_column("projects", "kept_at")
