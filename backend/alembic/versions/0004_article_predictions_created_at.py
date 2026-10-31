"""article_predictions.created_at

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # MODULE 5 (lot 6): prediction timestamps - lets the
    # ``GET /projects/{id}/articles/{aid}/prediction`` endpoint return the
    # most recent prediction when an article has several (inference re-run).
    op.add_column(
        "article_predictions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
    )
    op.create_index("ix_article_predictions_created_at", "article_predictions", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_article_predictions_created_at", table_name="article_predictions")
    op.drop_column("article_predictions", "created_at")
