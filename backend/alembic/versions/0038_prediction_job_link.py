"""Article predictions: link to the inference job (re-run + comparison, spec 2026-11-07).

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-07

``article_predictions.inference_job_id``: the job that produced the prediction
(SET NULL: deleting a job keeps its predictions, which are re-runnable history).
NULL = legacy data or predictions created outside a job; there is no
backfill (old jobs are not comparison references, by spec).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("article_predictions", sa.Column("inference_job_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_article_predictions_inference_job_id", "article_predictions", ["inference_job_id"]
    )
    op.create_foreign_key(
        "fk_article_predictions_inference_job_id",
        "article_predictions",
        "inference_jobs",
        ["inference_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_article_predictions_inference_job_id", "article_predictions")
    op.drop_index("ix_article_predictions_inference_job_id", table_name="article_predictions")
    op.drop_column("article_predictions", "inference_job_id")
