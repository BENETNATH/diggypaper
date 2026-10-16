"""searches.schedule / schedule_state (analyses programmées à la suite d'une autre recherche)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("searches", sa.Column("schedule", postgresql.JSONB(), nullable=True))
    op.add_column("searches", sa.Column("schedule_state", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("searches", "schedule_state")
    op.drop_column("searches", "schedule")
