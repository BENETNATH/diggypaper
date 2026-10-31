"""Searches named AAMMJJ-n; "pmc" source for texts imported from PMC

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-17 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # "Initial search" -> AAMMJJ-n (creation day, rank in the project)
    rows = bind.execute(
        sa.text(
            "SELECT id, project_id, created_at FROM searches WHERE name = 'Recherche initiale' "
            "ORDER BY project_id, created_at, id"
        )
    ).all()
    taken: dict = {}
    for sid, pid, created in rows:
        names = taken.setdefault(
            pid,
            {
                r[0]
                for r in bind.execute(
                    sa.text("SELECT name FROM searches WHERE project_id = :p"), {"p": pid}
                )
            },
        )
        prefix, n = created.strftime("%y%m%d"), 1
        while f"{prefix}-{n}" in names:
            n += 1
        names.add(f"{prefix}-{n}")
        bind.execute(
            sa.text("UPDATE searches SET name = :n WHERE id = :i"), {"n": f"{prefix}-{n}", "i": sid}
        )
    # Earlier PMC imports: the text was stored without a source (not counted "with text")
    op.execute(
        "UPDATE articles SET fulltext_source = 'pmc', "
        "fulltext_url = CASE WHEN pmcid IS NOT NULL "
        "THEN 'https://pmc.ncbi.nlm.nih.gov/articles/' || pmcid || '/' END "
        "WHERE fulltext_source IS NULL AND sections IS NOT NULL AND sections::text <> '[]'"
    )


def downgrade() -> None:
    pass
