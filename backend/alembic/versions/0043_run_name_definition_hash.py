"""Run name + frozen corpus definition on inference jobs.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-20

Spec 2026-11-20-analyses-runs-1-backend §1:

- ``inference_jobs.name`` VARCHAR(100) NULL — the run name (stripped);
  NULL = run predating the feature (legacy). Uniqueness per search is
  enforced at the API level only: no DB constraint.
- ``inference_jobs.definition_hash`` VARCHAR(32) NULL — frozen copy of
  ``searches.definition_hash`` at job creation; NULL when the search has
  no hash. Never recomputed after the fact.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inference_jobs", sa.Column("name", sa.String(length=100), nullable=True))
    op.add_column(
        "inference_jobs", sa.Column("definition_hash", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("inference_jobs", "definition_hash")
    op.drop_column("inference_jobs", "name")
