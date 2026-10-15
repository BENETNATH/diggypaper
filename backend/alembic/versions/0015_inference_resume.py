"""inference_jobs.resume_after : reprise d'une analyse interrompue

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inference_jobs", sa.Column("resume_after", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("inference_jobs", "resume_after")
