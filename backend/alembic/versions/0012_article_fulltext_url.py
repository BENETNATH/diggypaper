"""articles : lien public vers le texte intégral / PDF

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("fulltext_url", sa.String(), nullable=True))
    # textes déjà récupérés via PMC : lien reconstruit depuis le PMCID
    op.execute(
        "UPDATE articles SET fulltext_url = "
        "'https://pmc.ncbi.nlm.nih.gov/articles/' || pmcid || '/' "
        "WHERE fulltext_source IN ('pmc', 'europepmc') AND pmcid IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("articles", "fulltext_url")
