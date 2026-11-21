"""Personal API key (spec 2026-11-21 R1): users.api_key_hash (sha256, unique)
+ users.api_key_created_at. The plaintext key is never stored.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_users_api_key_hash", "users", ["api_key_hash"], unique=True)
    op.add_column(
        "users", sa.Column("api_key_created_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "api_key_created_at")
    op.drop_index("ix_users_api_key_hash", table_name="users")
    op.drop_column("users", "api_key_hash")
