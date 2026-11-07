"""Section embeddings (RAG — recherche sémantique, spec 2026-11-07).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-07

Activates the ``vector`` extension (pgvector) and creates
``section_embeddings``: one vector per corpus section, with a HNSW index
(cosine) for the dense half of the hybrid semantic search.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy import ClauseElement, DefaultClause
from sqlalchemy.ext.compiler import compiles

from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- DB-side default UUID (same pattern as 0036) ------------------
class _UuidGenExpr(ClauseElement):
    __visit_name__ = "uuid_gen_expr"


@compiles(_UuidGenExpr, "postgresql")
def _uuid_gen_pg(element, compiler, **kw):
    return "gen_random_uuid()"


@compiles(_UuidGenExpr, "sqlite")
def _uuid_gen_sqlite(element, compiler, **kw):
    return "NULL"


def _uuid_default() -> DefaultClause:
    return DefaultClause(_UuidGenExpr())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # pgvector extension + HNSW index exist only on Postgres (the production
        # dialect; the SQLite test schema keeps the embedding as JSON).
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "section_embeddings",
        sa.Column("id", sa.Uuid(), server_default=_uuid_default(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(384), nullable=True),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_section_embeddings_article_id", "section_embeddings", ["article_id"])
    if bind.dialect.name == "postgresql":
        # Dense search: HNSW (approximate, cosine) — PostgreSQL-only, kept here (not
        # in the model metadata) so the SQLite test schema stays portable.
        op.execute(
            "CREATE INDEX idx_section_embeddings_embedding_hnsw "
            "ON section_embeddings USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("idx_section_embeddings_embedding_hnsw", table_name="section_embeddings")
    op.drop_index("ix_section_embeddings_article_id", table_name="section_embeddings")
    op.drop_table("section_embeddings")
    # The extension is kept: other objects may use it, and dropping it would lock
    # the re-upgrade path.
