"""Vectorize progress (RAG — « indexation visible » + zéro re-probe).

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-08

Creates ``vectorize_progress``: one row per project, upserted by the vectorize
task to expose the live progress of a corpus (re)indexing run (``total`` /
``done`` / ``status``). A cheap status read replaces the previous
client-side probe (a fake semantic search that loaded every section).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vectorize_progress",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="IDLE"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("vectorize_progress")
