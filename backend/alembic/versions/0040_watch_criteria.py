"""watch criteria: configurable alert criteria + combine

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-08

Adds the configurable alert criteria to the watch (spec
2026-11-08-criteres-veille): ``criteria`` JSONB (list of criterion objects,
None = legacy rule) and ``combine`` ("AND" default | "OR"). Both nullable —
existing watches keep the legacy behaviour (primary field positive OR
confidence >= match_threshold), no data to convert.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Portable JSON: plain JSON (SQLite), JSONB on PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watches", sa.Column("criteria", JSONB, nullable=True))
    op.add_column("watches", sa.Column("combine", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("watches", "combine")
    op.drop_column("watches", "criteria")
