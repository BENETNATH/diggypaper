"""Model and version traceability (predictions + job snapshot)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-29 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("article_predictions", sa.Column("model_identifier", sa.String(), nullable=True))
    op.add_column("article_predictions", sa.Column("model_version", sa.String(), nullable=True))
    op.add_column("inference_jobs", sa.Column("model_info", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("inference_jobs", "model_info")
    op.drop_column("article_predictions", "model_version")
    op.drop_column("article_predictions", "model_identifier")
