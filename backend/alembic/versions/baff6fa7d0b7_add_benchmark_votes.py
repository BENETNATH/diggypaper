"""add_benchmark_votes

Revision ID: baff6fa7d0b7
Revises: 0034
Create Date: 2026-11-03 21:04:10.126082

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "baff6fa7d0b7"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "benchmark_votes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("benchmark_run_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "benchmark_run_id",
            "article_id",
            "user_id",
            name="uq_benchmark_votes_run_article_user",
        ),
    )
    op.create_index(
        "ix_benchmark_votes_run_id",
        "benchmark_votes",
        ["benchmark_run_id"],
        unique=False,
    )
    op.create_foreign_key(
        None,
        "benchmark_votes",
        "benchmark_runs",
        ["benchmark_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        None,
        "benchmark_votes",
        "articles",
        ["article_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        None,
        "benchmark_votes",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_benchmark_votes_run_id", table_name="benchmark_votes")
    op.drop_table("benchmark_votes")
