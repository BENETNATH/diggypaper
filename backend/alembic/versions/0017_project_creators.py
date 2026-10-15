"""Créateurs de projets (admin) vs invités (suppression auto sans projet)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-15 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("users", "invitations"):
        op.add_column(
            table,
            sa.Column(
                "can_create_projects", sa.Boolean(), server_default=sa.text("false"), nullable=False
            ),
        )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # Comptes existants : tous créateurs (rien ne change pour eux) ; l'admin peut les rétrograder.
    op.execute("UPDATE users SET can_create_projects = true")


def downgrade() -> None:
    op.drop_column("invitations", "can_create_projects")
    op.drop_column("users", "token_version")
    op.drop_column("users", "can_create_projects")
