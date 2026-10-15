"""articles.fulltext_source + import_jobs.kind (texte intégral complété par sources ouvertes)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("fulltext_source", sa.String(), nullable=True))
    op.execute(
        "UPDATE articles SET fulltext_source = 'pmc' WHERE sections IS NOT NULL AND sections::text <> '[]'"  # noqa: E501
    )
    op.add_column(
        "import_jobs", sa.Column("kind", sa.String(), nullable=True, server_default="import")
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "kind")
    op.drop_column("articles", "fulltext_source")
