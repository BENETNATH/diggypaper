"""Job traceability: created_by on imports / analyses / benchmarks.

Activity log (spec 2026-11-03-activite-tracabilite): who launched which job.
Nullable — jobs predating the feature keep NULL (never invented).

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-03 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("import_jobs", "inference_jobs", "benchmark_runs"):
        op.add_column(
            table,
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_created_by", table, ["created_by"])


def downgrade() -> None:
    for table in ("import_jobs", "inference_jobs", "benchmark_runs"):
        op.drop_index(f"ix_{table}_created_by", table)
        op.drop_column(table, "created_by")
