"""Search key indexes (lists per search, cascades) + one gold per (search, article)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("article_predictions", "human_annotations", "inference_jobs", "gold_articles")


def upgrade() -> None:
    for table in _TABLES:
        op.create_index(f"ix_{table}_search_id", table, ["search_id"])
    op.create_index("ix_search_articles_article_id", "search_articles", ["article_id"])
    op.create_index(
        "uq_gold_articles_search_article", "gold_articles", ["search_id", "article_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_gold_articles_search_article", table_name="gold_articles")
    op.drop_index("ix_search_articles_article_id", table_name="search_articles")
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_search_id", table_name=table)
