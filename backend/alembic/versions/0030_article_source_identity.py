"""Identité générale des articles : (project_id, source, native_id)

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-29 12:00:00.000000

Migration additive : ``pmid`` et son index unique restent. Le backfill (source='pubmed',
native_id=pmid) ne peut pas créer de doublon du triplet, l'unicité (project_id, pmid) le garantit.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("source", sa.String(), nullable=True))
    op.add_column("articles", sa.Column("native_id", sa.String(), nullable=True))
    op.add_column("import_jobs", sa.Column("source", sa.String(), nullable=True))
    op.execute("UPDATE articles SET source = 'pubmed', native_id = pmid WHERE pmid IS NOT NULL")
    op.execute(
        "UPDATE import_jobs SET source = 'pubmed' "
        "WHERE kind IS NULL OR kind IN ('import', 'citations')"
    )
    op.create_index(
        "uq_articles_project_source_native",
        "articles",
        ["project_id", "source", "native_id"],
        unique=True,
        postgresql_where=sa.text("native_id IS NOT NULL"),
        sqlite_where=sa.text("native_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_articles_project_source_native", table_name="articles")
    op.drop_column("import_jobs", "source")
    op.drop_column("articles", "native_id")
    op.drop_column("articles", "source")
