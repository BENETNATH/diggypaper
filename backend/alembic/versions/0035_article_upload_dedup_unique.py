"""Partial unique constraint on (project_id, origin, origin_ref) for upload dedup.

Revision ID: 0035
Revises: baff6fa7d0b7
Create Date: 2026-11-06

PARTIAL on origin='upload': for import origins the ``origin_ref`` is the import
job id, shared by every article of the same job (pubmed / references / crate /
citation) — a table-wide unique constraint fails on any multi-article import.
"""

from sqlalchemy import text

from alembic import op

revision = "0035"
down_revision = "baff6fa7d0b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_article_project_origin_ref",
        "articles",
        ["project_id", "origin", "origin_ref"],
        unique=True,
        postgresql_where=text("origin = 'upload' AND origin_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_article_project_origin_ref", table_name="articles")
