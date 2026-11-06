"""Add unique constraint on (project_id, origin, origin_ref) for upload dedup.

Revision ID: 0035
Revises: baff6fa7d0b7
Create Date: 2026-11-06
"""

from alembic import op

revision = "0035"
down_revision = "baff6fa7d0b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_article_project_origin_ref",
        "articles",
        ["project_id", "origin", "origin_ref"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_article_project_origin_ref", "articles")
