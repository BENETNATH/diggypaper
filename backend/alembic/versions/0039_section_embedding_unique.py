"""unique constraint on section_embeddings (article_id, section_index)

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-08

Guard against duplicate vectors per section: a concurrent vectorize run (manual
"Indexer le corpus" + the watch import hook) raced and left two rows per
(article, section), which the RRF search double-counted. The upsert in
vectorize_project relies on this constraint; legacy duplicates are purged in
the same transaction before it is created.
"""

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Purge legacy duplicates — keep the row with the smallest id per
    # (article_id, section_index) (UUIDv4 is not time-ordered; the survivor is
    # arbitrary, the next vectorize run reconciles it via the hash/model check).
    # Portable form (no DELETE ... USING): this file must also work on SQLite.
    op.execute(
        sa.text(
            """
            DELETE FROM section_embeddings se
            WHERE EXISTS (
                SELECT 1 FROM section_embeddings se2
                WHERE se2.article_id = se.article_id
                  AND se2.section_index = se.section_index
                  AND se2.id > se.id
            )
            """
        )
    )
    op.create_index(
        "uq_section_embeddings_article_idx",
        "section_embeddings",
        ["article_id", "section_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_section_embeddings_article_idx", table_name="section_embeddings")
