"""Triage policy + per-field confidences + per-search section embeddings.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-09

Spec 2026-11-09-triage-seuils-fonds-champs §1 (cumulative, see also spec
2026-11-09-sous-analyse-criteres — this single migration carries both):

- ``projects.triage_policy`` JSONB NULL — per-project triage presets
  ({"accept": 0.7|0.85|0.9|null, "reject": 0.1|0.15|0.25|null}); NULL =
  default 0.85/0.15. No backfill (R2: no retroactivity).
- ``article_predictions.field_confidences`` JSONB NULL — per-field
  confidences {field_key: float} frozen at prediction time; NULL = old
  predictions. No backfill.
- ``section_embeddings.search_id`` UUID NULL — the analysis owning the
  vector; NULL = corpus embedding (convention unchanged). The unique
  constraint is extended to (article_id, section_index, search_id): corpus
  rows (search_id NULL) stay distinct in the unique index (NULLs are not
  equal).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON: plain JSON (SQLite), JSONB on PostgreSQL.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("projects", sa.Column("triage_policy", JSONB, nullable=True))
    op.add_column("article_predictions", sa.Column("field_confidences", JSONB, nullable=True))
    op.add_column(
        "section_embeddings",
        sa.Column("search_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_section_embeddings_search_id", "section_embeddings", ["search_id"])
    op.create_foreign_key(
        "fk_section_embeddings_search_id",
        "section_embeddings",
        "searches",
        ["search_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # extend the concurrency guard to (article, section, analysis); the corpus
    # (search_id NULL) keeps its own guard on a partial index — NULLs are not
    # equal in the 3-column unique index (PostgreSQL 15+ has no per-constraint
    # NULLS NOT DISTINCT for table constraints, and SQLite has none at all)
    #
    # 0039 created the (article_id, section_index) guard as a UNIQUE INDEX on
    # PostgreSQL (op.create_index), while metadata-built databases (tests,
    # SQLite) carry it as a table constraint: target the right object per
    # dialect.
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.drop_index("uq_section_embeddings_article_idx", table_name="section_embeddings")
        op.create_index(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            ["article_id", "section_index", "search_id"],
            unique=True,
        )
    else:
        op.drop_constraint(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            ["article_id", "section_index", "search_id"],
        )
    op.create_index(
        "uq_section_embeddings_corpus",
        "section_embeddings",
        ["article_id", "section_index"],
        unique=True,
        postgresql_where=sa.text("search_id IS NULL"),
        sqlite_where=sa.text("search_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_section_embeddings_corpus", table_name="section_embeddings")
    # same dialect split as upgrade(): index on PostgreSQL, constraint otherwise
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.drop_index("uq_section_embeddings_article_idx", table_name="section_embeddings")
        op.create_index(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            ["article_id", "section_index"],
            unique=True,
        )
    else:
        op.drop_constraint(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_section_embeddings_article_idx",
            "section_embeddings",
            ["article_id", "section_index"],
        )
    op.drop_constraint("fk_section_embeddings_search_id", "section_embeddings", type_="foreignkey")
    op.drop_index("ix_section_embeddings_search_id", table_name="section_embeddings")
    op.drop_column("section_embeddings", "search_id")
    op.drop_column("article_predictions", "field_confidences")
    op.drop_column("projects", "triage_policy")
