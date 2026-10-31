"""Access control: deactivable accounts, owned/shared models, tracked invitations

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "model_registry",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
    )
    op.create_table(
        "model_shares",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Uuid(),
            sa.ForeignKey("model_registry.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_model_shares_model_id", "model_shares", ["model_id"])
    op.create_index("ix_model_shares_user_id", "model_shares", ["user_id"])
    op.create_index("ix_model_shares_project_id", "model_shares", ["project_id"])
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("language", sa.String(), server_default="fr"),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("role", sa.String()),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])
    # Current behavior kept: each existing project keeps use of the models already
    # registered; the admin can then remove these shares.
    op.execute(
        """
        INSERT INTO model_shares (id, model_id, project_id)
        SELECT gen_random_uuid(), m.id, p.id FROM model_registry m CROSS JOIN projects p
        WHERE m.is_active IS NOT FALSE
        """
    )


def downgrade() -> None:
    op.drop_table("invitations")
    op.drop_table("model_shares")
    op.drop_column("model_registry", "owner_id")
    op.drop_column("users", "is_active")
